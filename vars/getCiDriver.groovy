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

class CiDriver
{
  private repo
  private cmds
  private steps
  private nodes
  private workspaces
  private pull_request = null
  private submit_refspecs = null
  private submit_commit = null
  private submit_version = null

  CiDriver(steps, repo) {
    this.cmds = [:]
    this.repo = repo
    this.steps = steps
    this.nodes = [:]
    this.workspaces = [:]
  }

  public def get_change_request_info() {
    if (steps.env.CHANGE_URL == null
     || !steps.env.CHANGE_URL.contains('/pull-requests/')) {
     return null
    }
    def restUrl = steps.env.CHANGE_URL
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
    return info
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
    def ref = steps.env.GIT_COMMIT
    if (steps.env.CHANGE_TARGET != null) {
      ref = steps.env.CHANGE_TARGET
    }
    steps.sh(script: "${venv}/bin/python ${venv}/bin/ci-driver --color=always --workspace=\"${workspace}\""
                   + " checkout-source-tree"
                   + " --target-remote=\"${steps.env.GIT_URL}\""
                   + " --target-ref=\"${ref}\""
                   + clean_param)
    if (this.pull_request != null) {
      def author_time = this.pull_request.get('updatedDate', steps.currentBuild.timeInMillis) / 1000.0
      def commit_time = steps.currentBuild.startTimeInMillis / 1000.0
      def conf_params = ''
      if (steps.fileExists("${workspace}/cfg.yml")) {
        conf_params += " --config=\"${workspace}/cfg.yml\""
      }
      def extra_params = ''
      if (this.pull_request.containsKey('description')) {
        extra_params += " --change-request-description=\"${pull_request.description}\""
      }
      this.submit_refspecs = steps.sh(script: "${venv}/bin/python ${venv}/bin/ci-driver --color=always --workspace=\"${workspace}\""
                                            + conf_params
                                            + " prepare-source-tree"
                                            + " --target-remote=\"${steps.env.GIT_URL}\""
                                            + " --target-ref=\"${ref}\""
                                            + " --source-remote=\"${steps.env.GIT_URL}\""
                                            + " --source-ref=\"${steps.env.GIT_COMMIT}\""
                                            + " --change-request=\"${steps.env.CHANGE_ID}\""
                                            + " --change-request-title=\"${steps.env.CHANGE_TITLE}\""
                                            + " --author-name=\"${steps.env.CHANGE_AUTHOR}\""
                                            + " --author-email=\"${steps.env.CHANGE_AUTHOR_EMAIL}\""
                                            + " --author-date=\"@${author_time}\""
                                            + " --commit-date=\"@${commit_time}\""
                                            + extra_params,
                                      returnStdout: true).split("\\r?\\n").collect{it}
      this.submit_commit = this.submit_refspecs.remove(0)

      steps.checkout(scm: [
          $class: 'GitSCM',
          userRemoteConfigs: [[
              url: workspace,
            ]],
          branches: [[name: this.submit_commit]],
        ])

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
      this.pull_request = this.get_change_request_info()
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

      if (this.submit_refspecs != null && this.canMerge()) {
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

  private def canMerge() {
    def cur_cr_info = this.get_change_request_info()
    return !(cur_cr_info == null
          || cur_cr_info.fromRef == null
          || cur_cr_info.fromRef.latestCommit != steps.env.GIT_COMMIT
          || !cur_cr_info.canMerge)
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
