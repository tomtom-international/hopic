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

import org.jenkinsci.plugins.credentialsbinding.impl.CredentialNotFoundException

class ChangeRequest
{
  protected steps

  ChangeRequest(steps) {
    this.steps = steps
  }

  protected def shell_quote(word) {
    return "'" + word.replace("'", "'\\''") + "'"
  }

  protected def maySubmitImpl(target_commit, source_commit, allow_cache = true) {
    return !steps.sh(script: 'git log ' + shell_quote(target_commit) + '..' + shell_quote(source_commit) + " --pretty='%s' --reverse", returnStdout: true)
      .trim().split('\\r?\\n').find { subject ->
        if (subject.startsWith('fixup!') || subject.startsWith('squash!')) {
          return true
        }
    }
  }

  public def maySubmit(target_commit, source_commit, allow_cache = true) {
    return this.maySubmitImpl(target_commit, source_commit, allow_cache)
  }

  public def apply(venv, workspace, source_remote = null) {
    assert false : "Change request instance does not override apply()"
  }
}

class BitbucketPullRequest extends ChangeRequest
{
  private url
  private info = null
  private credentialsId

  BitbucketPullRequest(steps, url, credentialsId) {
    super(steps)
    this.url = url
    this.credentialsId = credentialsId
  }

  private def get_info(allow_cache = true) {
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
        authentication: credentialsId,
      ).content)
    def merge = steps.readJSON(text: steps.httpRequest(
        url: restUrl + '/merge',
        httpMode: 'GET',
        authentication: credentialsId,
      ).content)
    if (merge.containsKey('canMerge')) {
      info['canMerge'] = merge['canMerge']
    }
    info['author_time'] = info.getOrDefault('updatedDate', steps.currentBuild.timeInMillis) / 1000.0
    info['commit_time'] = steps.currentBuild.startTimeInMillis / 1000.0
    this.info = info
    return info
  }

  public def maySubmit(target_commit, source_commit, allow_cache = true) {
    def cur_cr_info = this.get_info(allow_cache)
    return !(!super.maySubmitImpl(target_commit, source_commit, allow_cache)
          || cur_cr_info == null
          || cur_cr_info.fromRef == null
          || cur_cr_info.fromRef.latestCommit != source_commit
          || !cur_cr_info.canMerge)
  }

  public def apply(venv, workspace, source_remote) {
    def change_request = this.get_info()
    def conf_params = ''
    if (steps.fileExists("${workspace}/cfg.yml")) {
      conf_params += " --config=\"${workspace}/cfg.yml\""
    }
    def extra_params = ''
    if (change_request.containsKey('description')) {
      extra_params += " --description=\"${change_request.description}\""
    }
    def source_refspec = steps.scm.userRemoteConfigs[0].refspec
    def (remote_ref, local_ref) = source_refspec.tokenize(':')
    if (remote_ref.startsWith('+'))
      remote_ref = remote_ref.substring(1)
    def output = steps.sh(script: shell_quote("${venv}/bin/python") + ' -m cidriver --color=always --workspace=' + shell_quote(workspace)
                                + conf_params
                                + ' prepare-source-tree'
                                + ' --author-name=' + shell_quote(steps.env.CHANGE_AUTHOR)
                                + ' --author-email=' + shell_quote(steps.env.CHANGE_AUTHOR_EMAIL)
                                + ' --author-date=' + shell_quote('@' + change_request.author_time)
                                + ' --commit-date=' + shell_quote('@' + change_request.commit_time)
                                + ' merge-change-request'
                                + ' --source-remote=' + shell_quote(source_remote)
                                + ' --source-ref=' + shell_quote(remote_ref)
                                + ' --change-request=' + shell_quote(steps.env.CHANGE_ID)
                                + ' --title=' + shell_quote(steps.env.CHANGE_TITLE)
                                + extra_params,
                          returnStdout: true).split("\\r?\\n").findAll{it.size() > 0}
    if (output.size() <= 0) {
      return null
    }
    def rv = [
        commit: output.remove(0),
      ]
    if (output.size() > 0) {
      rv.version = output.remove(0)
    }
    return rv
  }

}

class SpecialModalityRequest extends ChangeRequest
{
  private modality

  SpecialModalityRequest(steps, modality) {
    super(steps)
    this.modality = modality
  }

  public def apply(venv, workspace, source_remote = null) {
    def author_time = steps.currentBuild.timeInMillis / 1000.0
    def commit_time = steps.currentBuild.startTimeInMillis / 1000.0
    def conf_params = ''
    if (steps.fileExists("${workspace}/cfg.yml")) {
      conf_params += " --config=\"${workspace}/cfg.yml\""
    }
    def output = steps.sh(script: shell_quote("${venv}/bin/python") + ' -m cidriver --color=always --workspace=' + shell_quote(workspace)
                                + conf_params
                                + ' prepare-source-tree'
                                + ' --author-date=' + shell_quote('@' + author_time)
                                + ' --commit-date=' + shell_quote('@' + commit_time)
                                + ' apply-modality-change ' + shell_quote(modality),
                          returnStdout: true).split("\\r?\\n").findAll{it.size() > 0}
    if (output.size() <= 0) {
      return null
    }
    def rv = [
        commit: output.remove(0),
      ]
    if (output.size() > 0) {
      rv.version = output.remove(0)
    }
    return rv
  }
}

class CiDriver
{
  private repo
  private steps
  private cmds            = [:]
  private nodes           = [:]
  private workspaces      = [:]
  private stashes         = [:]
  private submit_version  = null
  private change          = null
  private source_commit   = "HEAD"
  private target_commit   = null
  private may_submit_result = null

  private final default_node_expr = "Linux && Docker"

  CiDriver(steps, repo, change = null) {
    this.repo = repo
    this.steps = steps
    this.change = change
  }

  private def get_change() {
    if (this.change == null) {
      if (steps.env.CHANGE_URL != null
       && steps.env.CHANGE_URL.contains('/pull-requests/'))
      {
        def httpServiceCredential = steps.scm.userRemoteConfigs[0].credentialsId
        try {
          steps.withCredentials([steps.usernamePassword(
              credentialsId: httpServiceCredential,
              usernameVariable: 'USERNAME',
              passwordVariable: 'PASSWORD',
              )]) {
          }
        } catch (CredentialNotFoundException e1) {
          try {
            steps.withCredentials([steps.usernamePassword(
                credentialsId: httpServiceCredential,
                keystoreVariable: 'KEYSTORE',
                )]) {
            }
          } catch (CredentialNotFoundException e2) {
            /* Fall back when this credential isn't usable for HTTP(S) Basic Auth */
            httpServiceCredential = 'tt_service_account_creds'
          }
        }
        this.change = new BitbucketPullRequest(steps, steps.env.CHANGE_URL, httpServiceCredential)
      }
      // FIXME: Don't rely on hard-coded build parameter, externalize this instead.
      else if (steps.params.MODALITY != null && steps.params.MODALITY != "NORMAL")
      {
        this.change = new SpecialModalityRequest(steps, steps.params.MODALITY)
      }
    }

    return this.change
  }

  private def shell_quote(word) {
    return "'" + word.replace("'", "'\\''") + "'"
  }

  public def install_prerequisites() {
    if (!this.cmds.containsKey(steps.env.NODE_NAME)) {
      def venv = steps.pwd(tmp: true) + "/cidriver-venv"
      def workspace = steps.pwd()
      steps.sh(script: 'python -m virtualenv --clear ' + shell_quote(venv) + '\n'
                     + shell_quote("${venv}/bin/python") + ' -m pip install ' + shell_quote(this.repo))
      this.cmds[steps.env.NODE_NAME] = "${venv}/bin/python ${venv}/bin/ci-driver --color=always --config=\"${workspace}/cfg.yml\" --workspace=\"${workspace}\""
    }
    return this.cmds[steps.env.NODE_NAME]
  }

  private def with_credentials(closure) {
    // Ensure
    try {
      steps.withCredentials([steps.usernamePassword(
          credentialsId: steps.scm.userRemoteConfigs[0].credentialsId,
          usernameVariable: 'USERNAME',
          passwordVariable: 'PASSWORD',
          )]) {
          def askpass_program = steps.pwd(tmp: true) + '/jenkins-git-askpass.sh'
          steps.writeFile(
              file: askpass_program,
              text: '''\
#!/bin/sh
case "$1" in
[Uu]sername*) echo ''' + shell_quote(steps.USERNAME) + ''' ;;
[Pp]assword*) echo ''' + shell_quote(steps.PASSWORD) + ''' ;;
esac
''')
          return steps.withEnv(["GIT_ASKPASS=${askpass_program}"]) {
            steps.sh(script: 'chmod 700 "${GIT_ASKPASS}"')
            def r = closure()
            steps.sh(script: 'rm "${GIT_ASKPASS}"')
            return r
          }
      }
    } catch (CredentialNotFoundException e1) {
      try {
        steps.withCredentials([steps.sshUserPrivateKey(
            credentialsId: steps.scm.userRemoteConfigs[0].credentialsId,
            keyFileVariable: 'KEYFILE',
            usernameVariable: 'USERNAME',
            passphraseVariable: 'PASSPHRASE',
            )]) {
            def tmpdir = steps.pwd(tmp: true)

            def askpass_program = "${tmpdir}/jenkins-git-ssh-askpass.sh"
            steps.writeFile(
                file: askpass_program,
                text: '''\
#!/bin/sh
echo ''' + shell_quote(steps.env.PASSPHRASE ?: '') + '''
''')

            def ssh_program = "${tmpdir}/jenkins-git-ssh.sh"
            steps.writeFile(
                file: ssh_program,
                text: '''\
#!/bin/sh
# SSH_ASKPASS might be ignored if DISPLAY is not set
if [ -z "${DISPLAY:-}" ]; then
DISPLAY=:123.456
export DISPLAY
fi
exec ssh -i '''
+ shell_quote(steps.KEYFILE)
+ (steps.env.USERNAME != null ? ''' -l ''' + shell_quote(steps.USERNAME) : '')
+ ''' -o StrictHostKeyChecking=no -o IdentitiesOnly=yes "$@"
''')

            return steps.withEnv(["SSH_ASKPASS=${askpass_program}", "GIT_SSH=${ssh_program}", "GIT_SSH_VARIANT=ssh"]) {
              steps.sh(script: 'chmod 700 "${GIT_SSH}" "${SSH_ASKPASS}"')
              def r = closure()
              steps.sh(script: 'rm "${GIT_SSH}" "${SSH_ASKPASS}"')
              return r
            }
        }
      } catch (CredentialNotFoundException e2) {
        // Ignore, hoping that we're dealing with a passwordless SSH credential stored at ~/.ssh/id_rsa
        return closure()
      }
    }
  }

  private def checkout(clean = false) {
    def cmd = this.install_prerequisites()

    def venv = steps.pwd(tmp: true) + "/cidriver-venv"
    def workspace = steps.pwd()
    this.with_credentials() {
      def target_remote = steps.scm.userRemoteConfigs[0].url
      def target_ref    = steps.env.CHANGE_TARGET ?: steps.env.BRANCH_NAME
      def clean_param = clean ? " --clean" : ""
      this.target_commit = steps.sh(script: shell_quote("${venv}/bin/python") + ' -m cidriver --color=always --workspace=' + shell_quote(workspace)
                                          + ' checkout-source-tree'
                                          + ' --target-remote=' + shell_quote(target_remote)
                                          + ' --target-ref=' + shell_quote(target_ref)
                                          + clean_param,
                                    returnStdout: true).trim()
      if (this.get_change() != null) {
        def submit_info = this.get_change().apply(venv, workspace, target_remote)
        if (submit_info == null)
        {
          try {
              if (steps.currentBuild.rawBuild.getCauses().get(0).properties['shortDescription'].contains('Started by timer')) {
                steps.currentBuild.rawBuild.delete()
              }
          } catch (Exception ex) {
            // Ignore issues
          }
          steps.currentBuild.result = 'ABORTED'
          steps.error('No changes to build')
        }

        steps.checkout(scm: [
            $class: 'GitSCM',
            userRemoteConfigs: [[
                url: workspace,
              ]],
            branches: [[name: submit_info.commit]],
          ])

        this.submit_version = submit_info.version
      }
    }
    return workspace
  }

  public def get_submit_version() {
    return this.submit_version
  }

  public def get_variants(phase = null) {
    def phase_arg = phase ? ' --phase=' + shell_quote(phase) : ""
    def cmd = this.install_prerequisites()
    return steps.sh(
        script: "${cmd} variants" + phase_arg,
        returnStdout: true,
      ).split("\\r?\\n")
  }

  public def has_change() {
    return this.get_change() != null
  }

  public def has_submittable_change() {
    if (this.may_submit_result == null) {
      assert !this.has_change() || (this.target_commit != null && this.source_commit != null)
      this.may_submit_result = this.has_change() && this.get_change().maySubmit(target_commit, source_commit, /* allow_cache =*/ false)
    }
    return this.may_submit_result
  }

  private def ensure_checkout(clean = false) {
    if (!this.workspaces.containsKey(steps.env.NODE_NAME)) {
      this.workspaces[steps.env.NODE_NAME] = this.checkout(clean)
    }
  }

  /**
   * Unstash everything previously stashed on other nodes that we didn't yet unstash here.
   */
  private def ensure_unstashed() {
    this.stashes.each { name, stash ->
      if (stash.nodes[steps.env.NODE_NAME]) {
        return
      }
      steps.dir(stash.dir) {
        steps.unstash(name)
      }
      this.stashes[name].nodes[steps.env.NODE_NAME] = true
    }
  }

  public def on_build_node(Map params = [:], closure) {
    def node_expr = this.nodes.collect { variant, node -> node }.join(" || ") ?: params.getOrDefault('default_node_expr', this.default_node_expr)
    return steps.node(node_expr) {
      this.ensure_checkout(params.getOrDefault('clean', false))
      this.ensure_unstashed()
      return closure()
    }
  }

  public def build(clean = false) {
    steps.ansiColor('xterm') {
      def phases = steps.node(default_node_expr) {
        def cmd = this.install_prerequisites()
        def workspace = steps.pwd()

        /*
         * We're splitting the enumeration of phases and variants from their execution in order to
         * enable Jenkins to execute the different variants within a phase in parallel.
         *
         * In order to do this we only check out the CI config file to the orchestrator node.
         */
        def scm = steps.checkout(steps.scm)
        steps.env.GIT_COMMIT          = scm.GIT_COMMIT
        steps.env.GIT_COMMITTER_NAME  = scm.GIT_COMMITTER_NAME
        steps.env.GIT_COMMITTER_EMAIL = scm.GIT_COMMITTER_EMAIL
        steps.env.GIT_AUTHOR_NAME     = scm.GIT_AUTHOR_NAME
        steps.env.GIT_AUTHOR_EMAIL    = scm.GIT_AUTHOR_EMAIL

        if (steps.env.CHANGE_TARGET) {
          this.source_commit = scm.GIT_COMMIT
        }

        def phases = steps.sh(
            script: "${cmd} phases",
            returnStdout: true,
          ).split("\\r?\\n").collect { phase ->
          [
            phase: phase,
            variants: this.get_variants(phase).collect { variant ->
              def meta = steps.readJSON(text: steps.sh(
                  script: "${cmd} getinfo --phase=" + shell_quote(phase) + ' --variant=' + shell_quote(variant),
                  returnStdout: true,
                ))
              def run_on_change = meta.getOrDefault('run-on-change', 'always')
              if (run_on_change instanceof Boolean) {
                run_on_change = run_on_change ? 'always' : 'never'
              }
              [
                variant: variant,
                label: meta.getOrDefault('node-label', default_node_expr),
                run_on_change: run_on_change,
              ]
            },
          ]
        }
        return phases
      }

      phases.each {
          def phase    = it.phase

          // Make sure steps exclusive to changes, or not intended to execute for changes, are skipped when appropriate
          def variants = it.variants.findAll {
            def run_on_change = it.run_on_change

            if (run_on_change == 'always') {
              return true
            } else if (run_on_change == 'never') {
              return !this.has_change()
            } else if (run_on_change == 'only') {
              if (this.source_commit == null
               || this.target_commit == null) {
                // Don't have enough information to determine whether this is a submittable change: assume it is
                return true
              }

              // Only allocate a node to determine submittability once
              if (this.may_submit_result == null) {
                this.on_build_node(default_node_expr: it.label, clean: clean) {
                  this.has_submittable_change()
                }
              }

              assert this.may_submit_result != null
              return this.may_submit_result
            }
            assert false : "Unknown 'run-on-change' option: ${run_on_change}"
          }
          if (variants.size() == 0) {
            return
          }

          steps.stage(phase) {
            def stepsForBuilding = variants.collectEntries {
              def variant = it.variant
              def label   = it.label
              [ "${phase}-${variant}": {
                if (this.nodes.containsKey(variant)) {
                  label = this.nodes[variant]
                }
                steps.node(label) {
                  steps.stage("${phase}-${variant}") {
                    def cmd = this.install_prerequisites()
                    ensure_checkout(clean)
                    if (!this.nodes.containsKey(variant)) {
                      this.nodes[variant] = steps.env.NODE_NAME
                    }

                    this.ensure_unstashed()

                    // Meta-data retrieval needs to take place on the executing node to ensure environment variable expansion happens properly
                    def meta = steps.readJSON(text: steps.sh(
                        script: "${cmd} getinfo --phase=" + shell_quote(phase) + ' --variant=' + shell_quote(variant),
                        returnStdout: true,
                      ))

                    steps.sh(script: "${cmd} build --phase=" + shell_quote(phase) + ' --variant=' + shell_quote(variant))

                    // FIXME: re-evaluate if we can and need to get rid of special casing for stashing
                    if (meta.containsKey('stash')) {
                      def dir   = meta.stash.getOrDefault('dir', this.workspaces[steps.env.NODE_NAME])
                      def name  = "${phase}-${variant}"
                      steps.dir(dir) {
                        def params = [
                            name: name,
                          ]
                        if (meta.stash.containsKey('includes')) {
                          params['includes'] = meta.stash.includes
                        }
                        steps.stash(params)
                      }
                      this.stashes[name] = [dir: dir, nodes: [(steps.env.NODE_NAME): true]]
                    }
                  }
                }
              }]
            }
            steps.parallel stepsForBuilding
          }
      }

      def node = this.nodes.find{true}
      if (!node) {
        return
      }
      this.on_build_node {
        if (this.has_submittable_change()) {
          steps.stage('submit') {
            this.with_credentials() {
              // addBuildSteps(steps.isMainlineBranch(steps.env.CHANGE_TARGET) || steps.isReleaseBranch(steps.env.CHANGE_TARGET))
              def cmd = this.install_prerequisites()
              steps.sh(script: "${cmd} submit")
            }
          }
        }
      }
    }
  }
}

/**
  * getCiDriver()
  */

def call(repo) {
  return new CiDriver(this, repo)
}
