/* Copyright (c) 2018 - 2019 TomTom N.V. (https://tomtom.com)
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

import groovy.json.JsonOutput
import org.jenkinsci.plugins.credentialsbinding.impl.CredentialNotFoundException

class ChangeRequest {
  protected steps

  ChangeRequest(steps) {
    this.steps = steps
  }

  protected def shell_quote(word) {
    return "'" + word.replace("'", "'\\''") + "'"
  }

  protected ArrayList line_split(String text) {
    return text.split('\\r?\\n') as ArrayList
  }

  protected def maySubmitImpl(target_commit, source_commit, allow_cache = true) {
    return !line_split(steps.sh(script: 'git log ' + shell_quote(target_commit) + '..' + shell_quote(source_commit) + " --pretty='%H:%s' --reverse", returnStdout: true)
      .trim()).find { line ->
        def (commit, subject) = line.split(':', 2)
        if (subject.startsWith('fixup!') || subject.startsWith('squash!')) {
          steps.println("\033[36m[info] not submitting because commit ${commit} is marked with 'fixup!' or 'squash!': ${subject}\033[39m")
          return true
        }
    }
  }

  public def maySubmit(target_commit, source_commit, allow_cache = true) {
    return this.maySubmitImpl(target_commit, source_commit, allow_cache)
  }

  public def apply(cmd, source_remote) {
    assert false : "Change request instance does not override apply()"
  }
}

class BitbucketPullRequest extends ChangeRequest {
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

    // Expand '@user' tokens in pull request description to 'Full Name <Full.Name@example.com>'
    // because we don't have this mapping handy when reading git commit messages.
    if (info.containsKey('description')) {
      def users = [:]

      def m = info.description =~ /(?<!\\)@(\w+)/
      def last_idx = 0
      def new_description = ''
      m.each { _, username ->
        if (!username) {
          return
        }

        if (!users.containsKey(username)) {
          def baseRestUrl = url
            .replaceFirst(/\/projects\/.*/, '/rest/api/1.0')
          users[username] = steps.readJSON(text: steps.httpRequest(
              url: "${baseRestUrl}/users/${username}",
              httpMode: 'GET',
              authentication: credentialsId,
            ).content)
        }
        def user = users[username]

        def str = user.getOrDefault('displayName', user.getOrDefault('name', username))
        if (user.emailAddress) {
          str = "${str} <${user.emailAddress}>"
        }

        new_description = new_description + info.description[last_idx..m.start() - 1] + str
        last_idx = m.end()
      }
      new_description = new_description + info.description[last_idx..-1]
      info.description = new_description
    }

    info['author_time'] = info.getOrDefault('updatedDate', steps.currentBuild.timeInMillis) / 1000.0
    info['commit_time'] = steps.currentBuild.startTimeInMillis / 1000.0
    this.info = info
    return info
  }

  public def maySubmit(target_commit, source_commit, allow_cache = true) {
    if (!super.maySubmitImpl(target_commit, source_commit, allow_cache)) {
      return false
    }
    def cur_cr_info = this.get_info(allow_cache)
    if (cur_cr_info == null
     || cur_cr_info.fromRef == null
     || cur_cr_info.fromRef.latestCommit != source_commit) {
      steps.println("\033[31m[error] failed to get pull request info from BitBucket for ${source_commit}\033[39m")
      return false
    }
    if (!cur_cr_info.canMerge) {
      steps.println("\033[36m[info] not submitting because the BitBucket merge criteria are not met\033[39m")
    }
    return cur_cr_info.canMerge
  }

  public def apply(cmd, source_remote) {
    def change_request = this.get_info()
    def extra_params = ''
    if (change_request.containsKey('description')) {
      extra_params += ' --description=' + shell_quote(change_request.description)
    }

    // Record approving reviewers for auditing purposes
    def approvers = change_request.getOrDefault('reviewers', []).findAll { reviewer ->
        return reviewer.approved && reviewer.lastReviewedCommit == change_request.fromRef.latestCommit
      }.collect { reviewer ->
        def str = reviewer.user.getOrDefault('displayName', reviewer.user.name)
        if (reviewer.user.emailAddress) {
          str = "${str} <${reviewer.user.emailAddress}>"
        }
        return str
      }.sort()
    approvers.each { approver ->
      extra_params += ' --approved-by=' + shell_quote(approver)
    }

    def source_refspec = steps.scm.userRemoteConfigs[0].refspec
    def (remote_ref, local_ref) = source_refspec.tokenize(':')
    if (remote_ref.startsWith('+'))
      remote_ref = remote_ref.substring(1)
    def output = line_split(steps.sh(script: cmd
                                + ' prepare-source-tree'
                                + ' --author-name=' + shell_quote(steps.env.CHANGE_AUTHOR)
                                + ' --author-email=' + shell_quote(steps.env.CHANGE_AUTHOR_EMAIL)
                                + ' --author-date=' + shell_quote(sprintf('@%.3f', change_request.author_time))
                                + ' --commit-date=' + shell_quote(sprintf('@%.3f', change_request.commit_time))
                                + ' merge-change-request'
                                + ' --source-remote=' + shell_quote(source_remote)
                                + ' --source-ref=' + shell_quote(remote_ref)
                                + ' --change-request=' + shell_quote(steps.env.CHANGE_ID)
                                + ' --title=' + shell_quote(steps.env.CHANGE_TITLE)
                                + extra_params,
                          returnStdout: true)).findAll{it.size() > 0}
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

class ModalityRequest extends ChangeRequest {
  private modality

  ModalityRequest(steps, modality) {
    super(steps)
    this.modality = modality
  }

  public def apply(cmd, source_remote) {
    def author_time = steps.currentBuild.timeInMillis / 1000.0
    def commit_time = steps.currentBuild.startTimeInMillis / 1000.0
    def output = line_split(steps.sh(script: cmd
                                + ' prepare-source-tree'
                                + ' --author-date=' + shell_quote(sprintf('@%.3f', author_time))
                                + ' --commit-date=' + shell_quote(sprintf('@%.3f', commit_time))
                                + ' apply-modality-change ' + shell_quote(modality),
                          returnStdout: true)).findAll{it.size() > 0}
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

class CiDriver {
  private repo
  private steps
  private base_cmds          = [:]
  private cmds               = [:]
  private nodes              = [:]
  private checkouts          = [:]
  private stashes            = [:]
  private worktree_bundles   = [:]
  private submit_version     = null
  private change             = null
  private source_commit      = "HEAD"
  private target_commit      = null
  private may_submit_result  = null
  private may_publish_result = null
  private config_file

  private final default_node_expr = "Linux && Docker"

  CiDriver(Map params = [:], steps, repo) {
    this.repo = repo
    this.steps = steps
    this.change = params.change
    this.config_file = params.getOrDefault('config', 'cfg.yml')
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
        this.change = new ModalityRequest(steps, steps.params.MODALITY)
      }
    }

    return this.change
  }

  private def shell_quote(word) {
    return "'" + word.replace("'", "'\\''") + "'"
  }

  protected ArrayList line_split(String text) {
    return text.split('\\r?\\n') as ArrayList
  }

  public def install_prerequisites() {
    if (!this.base_cmds.containsKey(steps.env.NODE_NAME)) {
      def venv = steps.pwd(tmp: true) + "/cidriver-venv"
      def workspace = steps.pwd()
      // Timeout prevents infinite downloads from blocking the build forever
      steps.timeout(time: 1, unit: 'MINUTES', activity: true) {
        steps.sh(script: """\
rm -rf ${shell_quote(venv)}
python -m virtualenv --clear ${shell_quote(venv)}
${shell_quote(venv)}/bin/python -m pip install ${shell_quote(this.repo)}
""")
      }
      this.base_cmds[steps.env.NODE_NAME] = shell_quote("${venv}/bin/python") + ' ' + shell_quote("${venv}/bin/ci-driver") + ' --color=always'
    }
    return this.base_cmds[steps.env.NODE_NAME]
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

    def tmpdir = steps.pwd(tmp: true)
    def venv = tmpdir + "/cidriver-venv"
    def workspace = steps.pwd()

    cmd += ' --workspace=' + shell_quote(workspace)
    cmd += ' --config=' + shell_quote("${workspace}/${config_file}")

    def params = ''
    if (clean) {
      params += ' --clean'
    }

    params += ' --target-remote=' + shell_quote(steps.scm.userRemoteConfigs[0].url)
    params += ' --target-ref='    + shell_quote(get_branch_name())

    steps.env.GIT_COMMIT = this.with_credentials() {
      this.target_commit = steps.sh(script: cmd
                                          + ' checkout-source-tree'
                                          + params,
                                    returnStdout: true).trim()
      if (this.get_change() != null) {
        def submit_info = this.get_change().apply(cmd, steps.scm.userRemoteConfigs[0].url)
        if (submit_info == null)
        {
          def timerCauses = steps.currentBuild.buildCauses.findAll { cause ->
            cause._class.contains('TimerTriggerCause')
          }
          if (timerCauses) {
            steps.currentBuild.rawBuild.delete()
          }

          steps.currentBuild.result = 'ABORTED'
          steps.error('No changes to build')
        }

        this.submit_version = submit_info.version
        return submit_info.commit
      }
      return this.target_commit
    }

    def code_dir_output = tmpdir + '/code-dir.txt'
    if (steps.sh(script: 'git config --get ci-driver.code.dir > ' + shell_quote(code_dir_output), returnStatus: true) == 0) {
      workspace = steps.readFile(code_dir_output).trim()
    }

    return [
        workspace: workspace,
        cmd: cmd,
      ]
  }

  public def get_submit_version() {
    return this.submit_version
  }

  public def has_change() {
    return this.get_change() != null
  }

  /**
   * @pre this has to be executed on a node the first time
   */
  public def has_submittable_change() {
    if (this.may_submit_result == null) {
      assert steps.env.NODE_NAME != null, "has_submittable_change must be executed on a node the first time"

      assert !this.has_change() || (this.target_commit != null && this.source_commit != null)
      this.may_submit_result = this.has_change() && this.get_change().maySubmit(target_commit, source_commit, /* allow_cache =*/ false)
    }
    return this.may_submit_result
  }

  /**
   * @pre this has to be executed on a node the first time
   */
  public def has_publishable_change() {
    if (this.may_publish_result == null) {
      assert steps.env.NODE_NAME != null, "has_publishable_change must be executed on a node the first time"

      def cmd = this.checkouts[steps.env.NODE_NAME].cmd
      def may_publish = steps.sh(
          script: "${cmd} may-publish",
          returnStatus: true,
        ) == 0
      this.may_publish_result = may_publish && this.has_submittable_change()
    }
    return this.may_publish_result
  }

  /**
   * @pre this has to be executed on a node
   */
  private def ensure_checkout(clean = false) {
    assert steps.env.NODE_NAME != null, "ensure_checkout must be executed on a node"

    if (!this.checkouts.containsKey(steps.env.NODE_NAME)) {
      this.checkouts[steps.env.NODE_NAME] = this.checkout(clean)
    }
    this.worktree_bundles.each { name, bundle ->
      if (bundle.nodes[steps.env.NODE_NAME]) {
        return
      }
      steps.unstash(name)
      def cmd = this.checkouts[steps.env.NODE_NAME].cmd
      steps.sh(
          script: "${cmd} unbundle-worktrees --bundle=worktree-transfer.bundle",
        )
      this.worktree_bundles[name].nodes[steps.env.NODE_NAME] = true
    }
    return this.checkouts[steps.env.NODE_NAME]
  }

  /**
   * @return name of target branch that we're building.
   */
  public String get_branch_name() {
    steps.env.CHANGE_TARGET ?: steps.env.BRANCH_NAME
  }

  /**
   * @return a lock name unique to the target repository
   */
  public String get_lock_name() {
    def repo_url  = steps.scm.userRemoteConfigs[0].url
    def repo_name = repo_url.tokenize('/')[-2..-1].join('/') - ~/\.git$/ // "${project}/${repo}"
    def branch    = get_branch_name()
    "${repo_name}/${branch}"
  }

  /**
   * @return a tuple of build name and build identifier
   *
   * The build identifier is just the stringified build number for builds on branches.
   * For builds on pull requests it's the PR number plus build number on this PR.
   */
  public Tuple get_build_id() {
    def last_item_in_project_name = steps.currentBuild.projectName
    def project_name = steps.currentBuild.fullProjectName
    def job_name = project_name.take(project_name.lastIndexOf(last_item_in_project_name)
                                                 .with { it < 2 ? project_name.size() : it - 1 })

    def branch = get_branch_name()
    String build_name = "${job_name}/${branch}".replaceAll(/\/|%2F/, ' :: ')

    String build_identifier = (steps.env.CHANGE_TARGET ? "PR-${steps.env.CHANGE_ID} " : '') + "${steps.currentBuild.number}"

    [build_name, build_identifier]
  }

  /**
   * Unstash everything previously stashed on other nodes that we didn't yet unstash here.
   *
   * @pre this has to be executed on a node
   */
  private def ensure_unstashed() {
    assert steps.env.NODE_NAME != null, "ensure_unstashed must be executed on a node"

    this.stashes.each { name, stash ->
      if (stash.nodes[steps.env.NODE_NAME]) {
        return
      }
      if (stash.dir) {
        steps.dir(stash.dir) {
          steps.unstash(name)
        }
      } else {
        steps.unstash(name)
      }
      this.stashes[name].nodes[steps.env.NODE_NAME] = true
    }
  }

  /**
   * @pre this has to be executed on a node
   */
  private def pin_variant_to_current_node(String variant) {
    assert steps.env.NODE_NAME != null, "pin_variant_to_current_node must be executed on a node"

    if (!this.nodes.containsKey(variant)) {
      this.nodes[variant] = steps.env.NODE_NAME
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

  public def build(Map buildParams = [:]) {
    def clean = buildParams.getOrDefault('clean', false)
    def default_node = buildParams.getOrDefault('default_node_expr', this.default_node_expr)
    steps.ansiColor('xterm') {
      def phases = steps.node(default_node) {
        def cmd = this.install_prerequisites()
        def workspace = steps.pwd()

        /*
         * We're splitting the enumeration of phases and variants from their execution in order to
         * enable Jenkins to execute the different variants within a phase in parallel.
         *
         * In order to do this we only check out the CI config file to the orchestrator node.
         */
        def scm = steps.checkout(steps.scm)
        // Don't trust Jenkin's scm.GIT_COMMIT because it sometimes lies
        steps.env.GIT_COMMIT          = steps.sh(script: 'git rev-parse HEAD', returnStdout: true).trim()
        steps.env.GIT_COMMITTER_NAME  = scm.GIT_COMMITTER_NAME
        steps.env.GIT_COMMITTER_EMAIL = scm.GIT_COMMITTER_EMAIL
        steps.env.GIT_AUTHOR_NAME     = scm.GIT_AUTHOR_NAME
        steps.env.GIT_AUTHOR_EMAIL    = scm.GIT_AUTHOR_EMAIL

        if (steps.env.CHANGE_TARGET) {
          this.source_commit = steps.env.GIT_COMMIT
        }

        cmd += ' --workspace=' + shell_quote("${workspace}")
        cmd += ' --config=' + shell_quote("${workspace}/${config_file}")

        return line_split(steps.sh(script: "${cmd} phases",
            returnStdout: true))
            .findAll{it.size() > 0}
            .collect { phase ->
          [
            phase: phase,
            variants: line_split(steps.sh(script: "${cmd} variants --phase=" + shell_quote(phase), returnStdout: true)).collect { variant ->
              def meta = steps.readJSON(text: steps.sh(
                  script: "${cmd} getinfo --phase=" + shell_quote(phase) + ' --variant=' + shell_quote(variant),
                  returnStdout: true,
                ))
              [
                variant: variant,
                label: meta.getOrDefault('node-label', default_node),
                run_on_change: meta.getOrDefault('run-on-change', 'always'),
              ]
            }
          ]
        }
      }

      // NOP as default
      def lock_if_necessary = { closure -> closure() }

      def is_change_only = { variant -> variant.run_on_change == 'only' }
      def change_only_phase = phases.find { phase -> phase.variants.any is_change_only }
      def change_only_step = change_only_phase ? change_only_phase.variants.find(is_change_only) : null

      def is_publishable_change = steps.node(change_only_step ? change_only_step.label : default_node) {
        this.ensure_checkout(clean)

        def is_publishable = this.has_publishable_change()

        if (is_publishable) {
          // Ensure a new checkout is performed because the target repository may change while waiting for the lock
          this.checkouts.remove(steps.env.NODE_NAME)
        } else if (change_only_step) {
          // Optimization: prevent duplicate checkout effort when no locking is required
          this.pin_variant_to_current_node(change_only_step.variant)
        }

        return is_publishable
      }

      if (is_publishable_change) {
        lock_if_necessary = { closure ->
          return steps.lock(get_lock_name()) {
            return closure()
          }
        }
      }

      def artifactoryBuildInfo = [:]

      lock_if_necessary {
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

              return is_publishable_change
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
                    def cmd = this.ensure_checkout(clean).cmd
                    this.pin_variant_to_current_node(variant)

                    this.ensure_unstashed()

                    // Meta-data retrieval needs to take place on the executing node to ensure environment variable expansion happens properly
                    def meta = steps.readJSON(text: steps.sh(
                        script: "${cmd} getinfo --phase=" + shell_quote(phase) + ' --variant=' + shell_quote(variant),
                        returnStdout: true,
                      ))

                    try {
                      if (meta.containsKey('with-credentials')) {
                        def creds = meta['with-credentials']
                        def user_var = creds.getOrDefault('username-variable', 'USERNAME')
                        def pass_var = creds.getOrDefault('password-variable', 'PASSWORD')
                        steps.withCredentials([steps.usernamePassword(
                              credentialsId: creds['id'],
                              usernameVariable: user_var,
                              passwordVariable: pass_var,
                              )]) {
                          steps.sh(script: cmd
                              + ' --whitelisted-var=' + shell_quote(user_var)
                              + ' --whitelisted-var=' + shell_quote(pass_var)
                              + ' build'
                              + ' --phase=' + shell_quote(phase)
                              + ' --variant=' + shell_quote(variant)
                            )
                        }
                      } else {
                        steps.sh(script: "${cmd} build --phase=" + shell_quote(phase) + ' --variant=' + shell_quote(variant))
                      }
                    } finally {
                      if (meta.containsKey('junit')) {
                        def results = meta.junit
                        steps.dir(this.checkouts[steps.env.NODE_NAME].workspace) {
                          meta.junit.each { result ->
                            steps.junit(result)
                          }
                        }
                      }
                    }

                    // FIXME: re-evaluate if we can and need to get rid of special casing for stashing
                    if (meta.containsKey('stash')) {
                      def name  = "${phase}-${variant}"
                      def params = [
                          name: name,
                        ]
                      if (meta.stash.containsKey('includes')) {
                        params['includes'] = meta.stash.includes
                      }
                      if (meta.stash.dir) {
                        steps.dir(meta.stash.dir) {
                          steps.stash(params)
                        }
                      } else {
                        steps.stash(params)
                      }
                      this.stashes[name] = [dir: meta.stash.dir, nodes: [(steps.env.NODE_NAME): true]]
                    }
                    if (meta.containsKey('worktrees')) {
                      def name = "${phase}-${variant}-worktree-transfer.bundle"
                      steps.stash(
                          name: name,
                          includes: 'worktree-transfer.bundle',
                        )
                      this.worktree_bundles[name] = [nodes: [(steps.env.NODE_NAME): true]]
                    }
                    def archiving_cfg = meta.containsKey('archive') ? 'archive' : meta.containsKey('fingerprint') ? 'fingerprint' : null
                    if (archiving_cfg) {
                      def artifacts = meta[archiving_cfg].artifacts
                      if (artifacts == null) {
                        steps.error("Archive configuration entry for ${phase}.${variant} does not contain 'artifacts' property")
                      }
                      steps.dir(this.checkouts[steps.env.NODE_NAME].workspace) {
                        artifacts.each { artifact ->
                          def pattern = artifact.pattern.replace('(*)', '*')
                          if (archiving_cfg == 'archive') {
                            steps.archiveArtifacts(
                                artifacts: pattern,
                                fingerprint: meta.archive.getOrDefault('fingerprint', true),
                              )
                          } else if (archiving_cfg == 'fingerprint') {
                            steps.fingerprint(pattern)
                          }
                        }
                        if (meta[archiving_cfg].containsKey('upload-artifactory')) {
                          def server_id = meta[archiving_cfg]['upload-artifactory'].id
                          if (server_id == null) {
                            steps.error("Artifactory upload configuration entry for ${phase}.${variant} does not contain 'id' property to identify Artifactory server")
                          }
                          def uploadSpec = JsonOutput.toJson([
                              files: artifacts.collect { artifact ->
                                def fileSpec = [
                                  pattern: artifact.pattern,
                                  target: artifact.target,
                                ]
                                if (fileSpec.target == null) {
                                  steps.error("Artifactory upload configuration entry for ${phase}.${variant} does not contain 'target' property to identify target repository")
                                }
                                if (artifact.props != null) {
                                  fileSpec.props = artifact.props
                                }
                                return fileSpec
                              }
                            ])
                          if (!artifactoryBuildInfo.containsKey(server_id)) {
                            def newBuildInfo = steps.Artifactory.newBuildInfo()
                            def (build_name, build_identifier) = get_build_id()
                            newBuildInfo.name = build_name
                            newBuildInfo.number = build_identifier
                            artifactoryBuildInfo[server_id] = newBuildInfo
                          }
                          def server = steps.Artifactory.server server_id
                          server.upload(spec: uploadSpec, buildInfo: artifactoryBuildInfo[server_id])
                          // Work around Artifactory Groovy bug
                          server = null
                        }
                      }
                    }
                  }
                }
              }]
            }
            steps.parallel stepsForBuilding
          }
        }

        if (this.may_submit_result != false) {
          this.on_build_node {
            if (this.has_submittable_change()) {
              steps.stage('submit') {
                this.with_credentials() {
                  // addBuildSteps(steps.isMainlineBranch(steps.env.CHANGE_TARGET) || steps.isReleaseBranch(steps.env.CHANGE_TARGET))
                  def cmd = this.ensure_checkout(clean).cmd
                  steps.sh(script: "${cmd} submit")
                }
              }
            }
          }
        }
      }

      if (artifactoryBuildInfo) {
        assert this.nodes : "When we have artifactory build info we expect to have execution nodes that it got produced on"
        this.on_build_node {
          def cmd = this.ensure_checkout(clean).cmd
          def config = steps.readJSON(text: steps.sh(
              script: "${cmd} show-config",
              returnStdout: true,
            ))

          artifactoryBuildInfo.each { server_id, buildInfo ->
            def promotion_config = config.getOrDefault('artifactory', [:]).getOrDefault('promotion', [:]).getOrDefault(server_id, [:])

            def server = steps.Artifactory.server server_id
            server.publishBuildInfo(buildInfo)
            if (promotion_config.containsKey('target-repo')
             && this.has_publishable_change()) {
              server.promote(
                  targetRepo:  promotion_config['target-repo'],
                  buildName:   buildInfo.name,
                  buildNumber: buildInfo.number,
                )
            }
            // Work around Artifactory Groovy bug
            server = null
          }
        }
      }
    }
  }
}

/**
  * getCiDriver()
  */

def call(Map params = [:], repo) {
  return new CiDriver(params, this, repo)
}
