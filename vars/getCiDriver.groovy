/* Copyright (c) 2018 - 2018 TomTom N.V. (https://tomtom.com)
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

class ChangeRequest
{
  protected steps

  ChangeRequest(steps) {
    this.steps = steps
  }

  public def maySubmit(target_commit, source_commit) {
    return !steps.sh(script: "git log ${target_commit}..${source_commit} --pretty=\"%s\" --reverse", returnStdout: true)
      .trim().split('\\r?\\n').find { subject ->
        if (subject.startsWith('fixup!') || subject.startsWith('squash!')) {
          return true
        }
    }
  }

  public def apply(venv, workspace, target_ref) {
    assert false : "Change request instance does not override apply()"
  }
}

class BitbucketPullRequest extends ChangeRequest
{
  private url
  private info

  BitbucketPullRequest(steps, url) {
    super(steps)
    this.url = url
  }

  public def get_info(allow_cache = true) {
    if (allow_cache && this.info) {
      return this.info
    }
    if (url == null
     || !url.contains('/pull-requests/')) {
     return null
    }
    def restUrl = url
      .replaceFirst(/(\/projects\/)/, '/rest/api/1.0$1')
      .replaceFirst(/\/overview$/, '')
    def info = steps.readJSON(text: steps.httpRequest(
        url: restUrl,
        httpMode: 'GET',
        authentication: 'tt_service_account_creds',
      ).content)
    def merge = steps.readJSON(text: steps.httpRequest(
        url: restUrl + '/merge',
        httpMode: 'GET',
        authentication: 'tt_service_account_creds',
      ).content)
    if (merge.containsKey('canMerge')) {
      info['canMerge'] = merge['canMerge']
    }
    info['author_time'] = info.get('updatedDate', steps.currentBuild.timeInMillis) / 1000.0
    info['commit_time'] = steps.currentBuild.startTimeInMillis / 1000.0
    this.info = info
    return info
  }

  public def maySubmit(target_commit, source_commit) {
    def cur_cr_info = this.get_info()
    return !(!super.maySubmit(target_commit, source_commit)
          || cur_cr_info == null
          || cur_cr_info.fromRef == null
          || cur_cr_info.fromRef.latestCommit != source_commit
          || !cur_cr_info.canMerge)
  }

  public def apply(venv, workspace, target_ref) {
    def change_request = this.get_info()
    def conf_params = ''
    if (steps.fileExists("${workspace}/cfg.yml")) {
      conf_params += " --config=\"${workspace}/cfg.yml\""
    }
    def extra_params = ''
    if (change_request.containsKey('description')) {
      extra_params += " --change-request-description=\"${change_request.description}\""
    }
    def submit_refspecs = steps.sh(script: "${venv}/bin/python ${venv}/bin/ci-driver --color=always --workspace=\"${workspace}\""
                                         + conf_params
                                         + " prepare-source-tree"
                                         + " --target-remote=\"${steps.env.GIT_URL}\""
                                         + " --target-ref=\"${target_ref}\""
                                         + " --source-remote=\"${steps.env.GIT_URL}\""
                                         + " --source-ref=\"${steps.env.GIT_COMMIT}\""
                                         + " --change-request=\"${steps.env.CHANGE_ID}\""
                                         + " --change-request-title=\"${steps.env.CHANGE_TITLE}\""
                                         + " --author-name=\"${steps.env.CHANGE_AUTHOR}\""
                                         + " --author-email=\"${steps.env.CHANGE_AUTHOR_EMAIL}\""
                                         + " --author-date=\"@${change_request.author_time}\""
                                         + " --commit-date=\"@${change_request.commit_time}\""
                                         + extra_params,
                                   returnStdout: true).split("\\r?\\n").collect{it}
    def submit_commit = submit_refspecs.remove(0)

    return [
        commit: submit_commit,
        refspecs: submit_refspecs,
      ]
  }

}

class CiDriver
{
  private repo
  private steps
  private cmds            = [:]
  private nodes           = [:]
  private workspaces      = [:]
  private submit_refspecs = null
  private submit_version  = null
  private change          = null

  CiDriver(steps, repo, change = null) {
    this.repo = repo
    this.steps = steps
    this.change = change
    if (this.change == null
     && steps.env.CHANGE_URL != null
     && steps.env.CHANGE_URL.contains('/pull-requests/')) {
      this.change = new BitbucketPullRequest(steps, steps.env.CHANGE_URL)
    }
  }

  public def install_prerequisites() {
    if (!this.cmds.containsKey(steps.env.NODE_NAME)) {
      def venv = steps.pwd(tmp: true) + "/cidriver-venv"
      def workspace = steps.pwd()
      steps.sh(script: "python -m virtualenv --clear ${venv}\n"
                     + "${venv}/bin/python -m pip install \"${this.repo}\"")
      this.cmds[steps.env.NODE_NAME] = "${venv}/bin/python ${venv}/bin/ci-driver --color=always --config=\"${workspace}/cfg.yml\" --workspace=\"${workspace}\""
    }
    return this.cmds[steps.env.NODE_NAME]
  }

  private def checkout(clean = false) {
    def cmd = this.install_prerequisites()

    def venv = steps.pwd(tmp: true) + "/cidriver-venv"
    def workspace = steps.pwd()
    def clean_param = clean ? " --clean" : ""
    def ref = steps.env.CHANGE_TARGET ?: steps.env.GIT_COMMIT
    steps.sh(script: "${venv}/bin/python ${venv}/bin/ci-driver --color=always --workspace=\"${workspace}\""
                   + " checkout-source-tree"
                   + " --target-remote=\"${steps.env.GIT_URL}\""
                   + " --target-ref=\"${ref}\""
                   + clean_param)
    if (this.change != null) {
      def submit_info = this.change.apply(venv, workspace, ref)
      steps.checkout(scm: [
          $class: 'GitSCM',
          userRemoteConfigs: [[
              url: workspace,
            ]],
          branches: [[name: submit_info.commit]],
        ])

      this.submit_refspecs = submit_info.refspecs
      def versions = []
      this.submit_refspecs.each { refspec ->
        def m = (refspec =~ /^[^:]*:refs\/tags\/(.+)/)
        if (m) {
          versions << m[0][1]
        }
      }
      if (versions.size() == 1) {
        this.submit_version = versions[0]
      }
    }
    return workspace
  }

  public def get_submit_version() {
    return this.submit_version
  }

  public def get_variants(phase = null) {
    def phase_arg = ""
    if (phase != null) {
      phase_arg = " --phase=\"${phase}\""
    }
    def cmd = this.install_prerequisites()
    return steps.sh(
        script: "${cmd} variants --phase=\"${phase}\"",
        returnStdout: true,
      ).split("\\r?\\n")
  }

  public def build(clean = false) {
    steps.ansiColor('xterm') {
      def orchestrator_cmd = this.install_prerequisites()

      /*
       * We're splitting the enumeration of phases and variants from their execution in order to
       * enable Jenkins to execute the different variants within a phase in parallel.
       */
      this.checkout()
      def phases = steps.sh(
          script: "${orchestrator_cmd} phases",
          returnStdout: true,
        ).split("\\r?\\n")

      phases.each { phase ->
          def variants = this.get_variants(phase)
          steps.stage(phase) {
            def stepsForBuilding = variants.collectEntries { variant ->
              [ "${phase}-${variant}": {
                def label = steps.readJSON(text: steps.sh(
                    script: "${orchestrator_cmd} getinfo --phase=\"${phase}\" --variant=\"${variant}\"",
                    returnStdout: true,
                  )).get('node-label', 'Linux && Docker')
                if (this.nodes.containsKey(variant)) {
                  label = this.nodes[variant]
                }
                steps.node(label) {
                  steps.stage("${phase}-${variant}") {
                    def cmd = this.install_prerequisites()
                    if (!this.workspaces.containsKey(steps.env.NODE_NAME)) {
                      this.workspaces[steps.env.NODE_NAME] = this.checkout(clean)
                    }
                    if (!this.nodes.containsKey(variant)) {
                      this.nodes[variant] = steps.env.NODE_NAME
                    }
                    // Meta-data retrieval needs to take place on the executing node to ensure environment variable expansion happens properly
                    def meta = steps.readJSON(text: steps.sh(
                        script: "${cmd} getinfo --phase=\"${phase}\" --variant=\"${variant}\"",
                        returnStdout: true,
                      ))
                    steps.sh(script: "${cmd} build --phase=\"${phase}\" --variant=\"${variant}\"")

                    // FIXME: get rid of special casing for stashing
                    if (meta.containsKey('stash')) {
                      steps.dir(meta['stash'].get('dir', this.workspaces[steps.env.NODE_NAME])) {
                        def params = [
                            name: variant,
                          ]
                        if (meta['stash'].containsKey('includes')) {
                          params['includes'] = meta['stash']['includes']
                        }
                        steps.stash(params)
                      }
                    }
                  }
                }
              }]
            }
            steps.parallel stepsForBuilding
          }
      }

      def target_commit = steps.env.CHANGE_TARGET ? "origin/${steps.env.CHANGE_TARGET}" : steps.env.GIT_COMMIT
      def source_commit = steps.env.CHANGE_TARGET ? steps.env.GIT_COMMIT : "HEAD"
      if (this.submit_refspecs != null && this.change.maySubmit(target_commit, source_commit)) {
        // addBuildSteps(steps.isMainlineBranch(steps.env.CHANGE_TARGET) || steps.isReleaseBranch(steps.env.CHANGE_TARGET))
        def refspecs = ""
        this.submit_refspecs.each { refspec ->
          refspecs += " --refspec=\"${refspec}\""
        }
        steps.sh(script: "${orchestrator_cmd} submit"
                         + " --target-remote=\"${steps.env.GIT_URL}\""
                         + refspecs)
      }
    }
  }
}

/**
  * getCiDriver()
  *
  * @return string usable for interpolation in shell scripts as ci-driver command
  */

def call(repo) {
  return new CiDriver(this, repo)
}
