/*
 * Copyright (c) 2018 - 2020 TomTom N.V. (https://tomtom.com)
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
    return "'" + (word as String).replace("'", "'\\''") + "'"
  }

  protected ArrayList line_split(String text) {
    return text.split('\\r?\\n') as ArrayList
  }

  protected def maySubmitImpl(target_commit, source_commit, allow_cache = true) {
    return !line_split(steps.sh(script: 'LC_ALL=C.UTF-8 git log ' + shell_quote(target_commit) + '..' + shell_quote(source_commit) + " --pretty='%H:%s' --reverse", returnStdout: true)
      .trim()).find { line ->
        if (!line) {
          return false
        }
        def (commit, subject) = line.split(':', 2)
        if (subject.startsWith('fixup!') || subject.startsWith('squash!')) {
          steps.println("\033[36m[info] not submitting because commit ${commit} is marked with 'fixup!' or 'squash!': ${subject}\033[39m")
          steps.currentBuild.description = "Not submitting: PR contains fixup! or squash!"
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

  public def notify_build_result(String job_name, String branch, String commit, String result) {
    // Default NOP
  }
}

class BitbucketPullRequest extends ChangeRequest {
  private url
  private info = null
  private credentialsId
  private restUrl = null
  private baseRestUrl = null
  private keyIds = [:]

  BitbucketPullRequest(steps, url, credentialsId) {
    super(steps)
    this.url = url
    this.credentialsId = credentialsId

    if (this.url != null) {
      this.restUrl = url
        .replaceFirst(/(\/projects\/)/, '/rest/api/1.0$1')
        .replaceFirst(/\/overview$/, '')
      this.baseRestUrl = this.restUrl
        .replaceFirst(/(\/rest)\/.*/, '$1')
    }
  }

  @NonCPS
  private List find_username_replacements(String message) {
    def m = message =~ /(?<!\\)(?<!\S)@(\w+)/

    def user_replacements = []

    m.each { match ->
      def username = match[1]
      if (!username) {
        return
      }

      user_replacements.add([
          username,
          m.start(),
          m.end(),
      ])
    }

    return user_replacements
  }

  private def get_info(allow_cache = true) {
    if (allow_cache && this.info) {
      return this.info
    }
    if (url == null
     || !url.contains('/pull-requests/')) {
     return null
    }
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
    if(merge.containsKey('vetoes')) {
      info['vetoes'] = merge['vetoes']
    }

    // Expand '@user' tokens in pull request description to 'Full Name <Full.Name@example.com>'
    // because we don't have this mapping handy when reading git commit messages.
    if (info.containsKey('description')) {
      def users = [:]

      def user_replacements = find_username_replacements(info.description)

      int last_idx = 0
      String new_description = ''
      user_replacements.each { repl ->
        def (username, start, end) = repl
        if (!users.containsKey(username)) {
          def response = steps.httpRequest(
              url: "${baseRestUrl}/api/1.0/users/${username}",
              httpMode: 'GET',
              authentication: credentialsId,
              validResponseCodes: '200,404',
            )
          def json = response.content ? steps.readJSON(text: response.content) : [:]
          if (response.status == 200) {
            users[username] = json
          } else {
            def errors = json.getOrDefault('errors', [])
            def msg = errors ? errors[0].getOrDefault('message', '') : ''
            steps.println("\033[31m[error] could not find BitBucket user '${username}'${msg ? ': ' : ''}${msg}\033[39m")
          }
        }

        if (users.containsKey(username)) {
          def user = users[username]

          def str = user.getOrDefault('displayName', user.getOrDefault('name', username))
          if (user.emailAddress) {
            str = "${str} <${user.emailAddress}>"
          }

          new_description = new_description + info.description[last_idx..start - 1] + str
          last_idx = end
        }
      }

      new_description = new_description + info.description[last_idx..-1]
      info.description = new_description.replace('\r\n', '\n')
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
      steps.currentBuild.description = "Not submitting: Bitbucket merge criteria not met"
      if (cur_cr_info.vetoes) {
        steps.println("\033[36m[info] the following merge condition(s) are not met: \033[39m")
        cur_cr_info.vetoes.each { veto ->
          if (veto.summaryMessage) {
            steps.println("\033[36m[info] summary: ${veto.summaryMessage}\033[39m")
            if (veto.detailedMessage) {
              steps.println("\033[36m[info]   details: ${veto.detailedMessage}\033[39m")
            }
          }
        }
      } else {
        steps.println("\033[36m[info] no information about why merge failed available\033[39m")
      }
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
        return reviewer.approved
      }.collect { reviewer ->
        def str = reviewer.user.getOrDefault('displayName', reviewer.user.name)
        if (reviewer.user.emailAddress) {
          str = "${str} <${reviewer.user.emailAddress}>"
        }
        return str + ':' + reviewer.lastReviewedCommit
      }.sort()
    approvers.each { approver ->
      extra_params += ' --approved-by=' + shell_quote(approver)
    }

    def source_refspec = steps.scm.userRemoteConfigs[0].refspec
    def (remote_ref, local_ref) = source_refspec.tokenize(':')
    if (remote_ref.startsWith('+'))
      remote_ref = remote_ref.substring(1)
    def cr_author = change_request.getOrDefault('author', [:]).getOrDefault('user', [:])
    def output = line_split(steps.sh(script: cmd
                                + ' prepare-source-tree'
                                + ' --author-name=' + shell_quote(cr_author.getOrDefault('displayName', steps.env.CHANGE_AUTHOR))
                                + ' --author-email=' + shell_quote(cr_author.getOrDefault('emailAddress', steps.env.CHANGE_AUTHOR_EMAIL))
                                + ' --author-date=' + shell_quote(String.format("@%.3f", change_request.author_time))
                                + ' --commit-date=' + shell_quote(String.format("@%.3f", change_request.commit_time))
                                + ' merge-change-request'
                                + ' --source-remote=' + shell_quote(source_remote)
                                + ' --source-ref=' + shell_quote(remote_ref)
                                + ' --change-request=' + shell_quote(change_request.getOrDefault('id', steps.env.CHANGE_ID))
                                + ' --title=' + shell_quote(change_request.getOrDefault('title', steps.env.CHANGE_TITLE))
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

  public def notify_build_result(String job_name, String branch, String commit, String result) {
    def state = (result == 'STARTING'
        ? 'INPROGRESS'
        : (result == 'SUCCESS' ? 'SUCCESSFUL' : 'FAILED')
        )

    def description = steps.currentBuild.description
    if (!description) {
      if        (result == 'STARTING') {
        description = 'The build is in progress...'
      } else if (result == 'SUCCESS') {
        description = 'This change request looks good.'
      } else if (result == 'UNSTABLE') {
        description = 'This change request has test failures.'
      } else if (result == 'FAILURE') {
        description = 'There was a failure building this change request.'
      } else if (result == 'ABORTED') {
        description = 'The build of this change request was aborted.'
      } else {
        description = 'Something is wrong with the build of this change request.'
      }
    }

    // Derive 'key' compatible with the BitBucket branch source plugin
    def key = "${job_name}/${branch}"
    if (!this.keyIds[key]) {
      // We could use java.security.MessageDigest instead of relying on a node. But that requires extra script approvals.
      assert steps.env.NODE_NAME != null, "notify_build_result must be executed on a node the first time"
      this.keyIds[key] = steps.sh(script: "echo -n ${shell_quote(key)} | md5sum", returnStdout: true).substring(0, 32)
    }
    def keyid = this.keyIds[key]

    def build_status = JsonOutput.toJson([
        state: state,
        key: keyid,
        url: steps.env.BUILD_URL,
        name: steps.currentBuild.fullDisplayName,
        description: description,
      ])
    steps.httpRequest(
        url: "${baseRestUrl}/build-status/1.0/commits/${commit}",
        httpMode: 'POST',
        contentType: 'APPLICATION_JSON',
        requestBody: build_status,
        authentication: credentialsId,
        validResponseCodes: '204',
      )
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
    def prepare_cmd = (cmd
      + ' prepare-source-tree'
      + ' --author-date=' + shell_quote(String.format("@%.3f", author_time))
      + ' --commit-date=' + shell_quote(String.format("@%.3f", commit_time))
    )
    def full_cmd = "${prepare_cmd} apply-modality-change ${shell_quote(modality)}"
    if (modality == 'BUMP_VERSION') {
      full_cmd = "${prepare_cmd} bump-version"
    }
    def output = line_split(steps.sh(script: full_cmd,
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
  private bitbucket_api_credential_id  = null

  private final default_node_expr = "Linux && Docker"

  CiDriver(Map params = [:], steps, repo) {
    this.repo = repo
    this.steps = steps
    this.change = params.change
    this.config_file = params.config
    this.bitbucket_api_credential_id = params.getOrDefault('bb_api_cred_id', 'tt_service_account_creds')
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
            httpServiceCredential = this.bitbucket_api_credential_id
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
    return "'" + (word as String).replace("'", "'\\''") + "'"
  }

  protected ArrayList line_split(String text) {
    return text.split('\\r?\\n') as ArrayList
  }

  public def with_hopic(closure) {
    assert steps.env.NODE_NAME != null, "with_hopic must be executed on a node"

    if (!this.base_cmds.containsKey(steps.env.NODE_NAME)) {
      def venv = steps.pwd(tmp: true) + "/hopic-venv"
      def workspace = steps.pwd()
      // Timeout prevents infinite downloads from blocking the build forever
      steps.timeout(time: 1, unit: 'MINUTES', activity: true) {
        // Use the exact same Hopic version on every build node
        if (this.repo.startsWith("git+") && this.repo !=~ /.*@[0-9a-fA-F]{40}/) {
          // Split on the last '@' only
          def split = this.repo[4..-1].split('@')
          def (remote, ref) = [split[0..-2].join('@'), split[-1]]
          def commit = line_split(steps.sh(script: "git ls-remote ${shell_quote(remote)}", returnStdout: true)).find { line ->
            def (hash, remote_ref) = line.split('\t')
            return (remote_ref == ref || remote_ref == "refs/heads/${ref}" || remote_ref == "refs/tags/${ref}")
          }
          if (commit != null)
          {
            def (hash, remote_ref) = commit.split('\t')
            this.repo = "git+${remote}@${hash}"
          }
        }

        steps.sh(script: """\
LC_ALL=C.UTF-8
export LC_ALL
rm -rf ${shell_quote(venv)}
python3 -m virtualenv --clear ${shell_quote(venv)}
cd /
${shell_quote(venv)}/bin/python -m pip install ${shell_quote(this.repo)}
""")
      }

      def cmd = 'LC_ALL=C.UTF-8 ' + shell_quote("${venv}/bin/python") + ' ' + shell_quote("${venv}/bin/hopic") + ' --color=always'
      if (this.config_file != null) {
        cmd += ' --workspace=' + shell_quote(workspace)
        def config_file_path = shell_quote(this.config_file.startsWith('/') ? "${config_file}" : "${workspace}/${config_file}")
        cmd += ' --config=' + "${config_file_path}"
      }
      this.base_cmds[steps.env.NODE_NAME] = cmd
    }

    return closure(this.base_cmds[steps.env.NODE_NAME])
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

  private def subcommand_with_credentials(String cmd, String subcmd, credentials) {
    def creds_info = credentials.collect({ currentCredential ->
      def credential_id = currentCredential['id']
      def type          = currentCredential['type']

      final white_listed_var = '--whitelisted-var='
      if (type == 'username-password') {
        def user_var = currentCredential['username-variable']
        def pass_var = currentCredential['password-variable']
        return [white_listed_vars: white_listed_var + shell_quote(user_var) + ' ' + white_listed_var + shell_quote(pass_var),
          with_credentials: steps.usernamePassword(
            credentialsId: credential_id,
            usernameVariable: user_var,
            passwordVariable: pass_var,)
        ]
      } else if (type == 'file') {
        def file_var = currentCredential['filename-variable']
        return [white_listed_vars: white_listed_var + shell_quote(file_var),
          with_credentials: steps.file(
            credentialsId: credential_id,
            variable: file_var,)
        ]
      } else if (type == 'string') {
        def string_var = currentCredential['string-variable']
        return [white_listed_vars: white_listed_var + shell_quote(string_var),
          with_credentials: steps.string(
            credentialsId: credential_id,
            variable: string_var,)
        ]
      }
    })

    if (creds_info.size() == 0) {
      return steps.sh(script: "${cmd} ${subcmd}")
    }

    try {
      return steps.withCredentials(creds_info*.with_credentials) {
        steps.sh(script: cmd
          + ' ' + creds_info*.white_listed_vars.join(" ")
          + ' ' + subcmd)
      }
    }
    catch (CredentialNotFoundException e) {
      steps.println("\033[31m[error] credential '${credentials*.id}' does not exist or is not of type '${credentials*.type}'\033[39m")
      throw e
    }
  }

  private def checkout(String cmd, clean = false) {
    def tmpdir = steps.pwd(tmp: true)
    def workspace = steps.pwd()

    def params = ''
    if (clean) {
      params += ' --clean'
    }

    if (this.has_change()) {
      params += ' --ignore-initial-submodule-checkout-failure'
    }

    def target_ref = get_branch_name()
    if (!target_ref) {
      steps.println('\033[36m[info] target branch is not specified; using GIT_COMMIT.\033[39m')
      target_ref = steps.env.GIT_COMMIT
    }

    params += ' --target-remote=' + shell_quote(steps.scm.userRemoteConfigs[0].url)
    params += ' --target-ref='    + shell_quote(target_ref)

    steps.env.GIT_COMMIT = this.with_credentials() {
      this.target_commit = steps.sh(script: cmd
                                          + ' checkout-source-tree'
                                          + params,
                                    returnStdout: true).trim()
      if (this.get_change() != null) {
        def submit_info = this.get_change().apply(cmd, steps.scm.userRemoteConfigs[0].url)
        if (submit_info == null)
        {
          // Marking the build as ABORTED _before_ deleting it to prevent an exception from reincarnating it
          steps.currentBuild.result = 'ABORTED'

          def timerCauses = steps.currentBuild.buildCauses.findAll { cause ->
            cause._class.contains('TimerTriggerCause')
          }
          if (timerCauses) {
            steps.currentBuild.rawBuild.delete()
          }

          steps.error('No changes to build')
        }

        this.submit_version = submit_info.version
        return submit_info.commit
      }
      return this.target_commit
    }

    // Ensure any required extensions are available
    steps.sh(script: "${cmd} install-extensions")

    def code_dir_output = tmpdir + '/code-dir.txt'
    if (steps.sh(script: 'LC_ALL=C.UTF-8 git config --get hopic.code.dir > ' + shell_quote(code_dir_output), returnStatus: true) == 0) {
      workspace = steps.readFile(code_dir_output).trim()
    }

    return workspace
  }

  public def get_submit_version() {
    return this.submit_version
  }

  public def has_change() {
    return this.get_change() != null
  }

  private def is_build_a_replay() {
    def r = steps.currentBuild.buildCauses.any{ cause -> cause._class.contains('ReplayCause') }
    if (r) {
      steps.println("\033[36m[info] not submitting because this build is a replay of another build.\033[39m")
      steps.currentBuild.description = "Not submitting: this build is a replay"
    }
    return r
  }

  /**
   * @pre this has to be executed on a node the first time
   */
  public def has_submittable_change() {
    if (this.may_submit_result == null) {
      assert steps.env.NODE_NAME != null, "has_submittable_change must be executed on a node the first time"

      assert !this.has_change() || (this.target_commit != null && this.source_commit != null)
      this.may_submit_result = this.has_change() && this.get_change().maySubmit(target_commit, source_commit, /* allow_cache =*/ false) && !this.is_build_a_replay()
      if (this.may_submit_result) {
        steps.println("\033[36m[info] submitting the commits since all merge criteria are met\033[39m")
        steps.currentBuild.description = "Submitting: all merge criteria are met"
      }
    }
    this.may_submit_result = this.may_submit_result && steps.currentBuild.currentResult == 'SUCCESS'
    return this.may_submit_result
  }

  /**
   * @pre this has to be executed on a node the first time
   */
  public def has_publishable_change() {
    if (this.may_publish_result == null) {
      assert steps.env.NODE_NAME != null, "has_publishable_change must be executed on a node the first time"

      def may_publish = this.with_hopic { cmd ->
        return steps.sh(
            script: "${cmd} may-publish",
            returnStatus: true,
          ) == 0
      }
      this.may_publish_result = may_publish && this.has_submittable_change()
    }
    return this.may_publish_result
  }

  /**
   * @pre this has to be executed on a node
   */
  private def ensure_checkout(String cmd, clean = false) {
    assert steps.env.NODE_NAME != null, "ensure_checkout must be executed on a node"

    if (!this.checkouts.containsKey(steps.env.NODE_NAME)) {
      this.checkouts[steps.env.NODE_NAME] = this.checkout(cmd, clean)
    }
    this.worktree_bundles.each { name, bundle ->
      if (bundle.nodes[steps.env.NODE_NAME]) {
        return
      }
      steps.unstash(name)
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
   * @return name of job that we're building
   */
  public String get_job_name() {
    def last_item_in_project_name = steps.currentBuild.projectName
    def project_name = steps.currentBuild.fullProjectName
    return project_name.take(project_name.lastIndexOf(last_item_in_project_name)
                                         .with { it < 2 ? project_name.size() : it - 1 })
  }

  /**
   * @return a tuple of build name and build identifier
   *
   * The build identifier is just the stringified build number for builds on branches.
   * For builds on pull requests it's the PR number plus build number on this PR.
   */
  public Tuple get_build_id() {
    def job_name = get_job_name()
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
      steps.dir(stash.dir) {
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

  private void archive_artifacts_if_enabled(Map meta, String workspace, boolean error_occurred, Closure get_build_info) {
    def archiving_cfg = meta.containsKey('archive') ? 'archive' : meta.containsKey('fingerprint') ? 'fingerprint' : null
    if (!archiving_cfg) {
      return
    }

    def upload_on_failure = meta[archiving_cfg].getOrDefault('upload-on-failure', false)
    if (!upload_on_failure && (error_occurred || steps.currentBuild.currentResult != 'SUCCESS')) {
      return
    }

    def artifacts = meta[archiving_cfg].artifacts
    if (artifacts == null) {
      steps.error("Archive configuration entry for ${phase}.${variant} does not contain 'artifacts' property")
    }
    steps.dir(workspace) {
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
        def buildInfo = get_build_info(server_id)
        def server = steps.Artifactory.server server_id
        server.upload(spec: uploadSpec, buildInfo: buildInfo)
        // Work around Artifactory Groovy bug
        server = null
      }
    }
  }

  public def on_build_node(Map params = [:], closure) {
    def node_expr = this.nodes.collect { variant, node -> node }.join(" || ") ?: params.getOrDefault('default_node_expr', this.default_node_expr)
    return steps.node(node_expr) {
      return this.with_hopic { cmd ->
        this.ensure_checkout(cmd, params.getOrDefault('clean', false))
        this.ensure_unstashed()
        return closure(cmd)
      }
    }
  }

  public def build(Map buildParams = [:]) {
    def clean = buildParams.getOrDefault('clean', false)
    def default_node = buildParams.getOrDefault('default_node_expr', this.default_node_expr)
    steps.ansiColor('xterm') {
      def (phases, is_publishable_change) = steps.node(default_node) {
        return this.with_hopic { cmd ->
          def workspace = steps.pwd()

          /*
           * We're splitting the enumeration of phases and variants from their execution in order to
           * enable Jenkins to execute the different variants within a phase in parallel.
           *
           * In order to do this we only check out the CI config file to the orchestrator node.
           */
          def scm = steps.checkout(steps.scm)
          // Don't trust Jenkin's scm.GIT_COMMIT because it sometimes lies
          steps.env.GIT_COMMIT          = steps.sh(script: 'LC_ALL=C.UTF-8 git rev-parse HEAD', returnStdout: true).trim()
          steps.env.GIT_COMMITTER_NAME  = scm.GIT_COMMITTER_NAME
          steps.env.GIT_COMMITTER_EMAIL = scm.GIT_COMMITTER_EMAIL
          steps.env.GIT_AUTHOR_NAME     = scm.GIT_AUTHOR_NAME
          steps.env.GIT_AUTHOR_EMAIL    = scm.GIT_AUTHOR_EMAIL

          if (steps.env.CHANGE_TARGET) {
            this.source_commit = steps.env.GIT_COMMIT
          }

          // Force a full based checkout & change application, instead of relying on the checkout done above, to ensure that we're building the list of phases and
          // variants to execute (below) using the final config file.
          this.ensure_checkout(cmd, clean)

          def phases = steps.readJSON(text: steps.sh(script: "${cmd} getinfo",
              returnStdout: true))
              .collect { phase, variants ->
            [
              phase: phase,
              variants: variants.collect { variant, meta ->
                [
                  variant: variant,
                  label: meta.getOrDefault('node-label', default_node),
                  run_on_change: meta.getOrDefault('run-on-change', 'always'),
                ]
              }
            ]
          }

          def is_publishable = this.has_publishable_change()

          if (is_publishable) {
            // Ensure a new checkout is performed because the target repository may change while waiting for the lock
            this.checkouts.remove(steps.env.NODE_NAME)
          }

          // Report start of build. _Must_ come after having determined whether this build is submittable and
          // publishable, because it may affect the result of the submittability check.
          if (this.change != null) {
            this.change.notify_build_result(get_job_name(), steps.env.CHANGE_BRANCH, this.source_commit, 'STARTING')
            this.change.notify_build_result(get_job_name(), steps.env.CHANGE_TARGET, steps.env.GIT_COMMIT, 'STARTING')
          }

          return [phases, is_publishable]
        }
      }

      // NOP as default
      def lock_if_necessary = { closure -> closure() }

      if (is_publishable_change) {
        lock_if_necessary = { closure ->
          return steps.lock(get_lock_name()) {
            return closure()
          }
        }
      }

      def artifactoryBuildInfo = [:]

      try {
        lock_if_necessary {
          phases.each {
            def phase    = it.phase
            def is_build_successful = steps.currentBuild.currentResult == 'SUCCESS'
            // Make sure steps exclusive to changes, or not intended to execute for changes, are skipped when appropriate
            def variants = it.variants.findAll { variant ->
              def run_on_change = variant.run_on_change

              if (run_on_change == 'always') {
                return true
              } else if (run_on_change == 'never') {
                return !this.has_change()
              } else if (run_on_change == 'only' || run_on_change == 'new-version-only') {
                if (is_build_successful) {
                  if (this.source_commit == null
                   || this.target_commit == null) {
                    // Don't have enough information to determine whether this is a submittable change: assume it is
                    return true
                  }
                  if (run_on_change == 'new-version-only') {
                    def version = this.get_submit_version()
                    if (version != null
                     && version ==~ /^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-(?:[-0-9a-zA-Z]+(?:\.[-0-9a-zA-Z])*))(?:\+(?:[-0-9a-zA-Z]+(?:\.[-0-9a-zA-Z])*))?$/) {
                      // Pre-release versions are not new versions, skip
                      return false
                    }
                  }
                  return is_publishable_change
                } else {
                  steps.println("Skipping variant ${variant.variant} in ${phase} because build is not successful")
                  return false
                }
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
                      this.with_hopic { cmd ->
                        final workspace = this.ensure_checkout(cmd, clean)
                        this.pin_variant_to_current_node(variant)

                        this.ensure_unstashed()

                        // Meta-data retrieval needs to take place on the executing node to ensure environment variable expansion happens properly
                        def meta = steps.readJSON(text: steps.sh(
                            script: "${cmd} getinfo --phase=" + shell_quote(phase) + ' --variant=' + shell_quote(variant),
                            returnStdout: true,
                          ))

                        def error_occurred = false
                        try {
                          this.subcommand_with_credentials(
                              cmd,
                              'build'
                            + ' --phase=' + shell_quote(phase)
                            + ' --variant=' + shell_quote(variant)
                            , meta.getOrDefault('with-credentials', []))
                        } catch(Exception e) {
                          error_occurred = true // Jenkins only sets its currentResult to Failure after all user code is executed
                          throw e
                        } finally {
                          if (meta.containsKey('junit')) {
                            def results = meta.junit
                            steps.dir(workspace) {
                              meta.junit.each { result ->
                                steps.junit(result)
                              }
                            }
                          }
                          this.archive_artifacts_if_enabled(meta, workspace, error_occurred, { server_id ->
                            if (!artifactoryBuildInfo.containsKey(server_id)) {
                              def newBuildInfo = steps.Artifactory.newBuildInfo()
                              def (build_name, build_identifier) = get_build_id()
                              newBuildInfo.name = build_name
                              newBuildInfo.number = build_identifier
                              artifactoryBuildInfo[server_id] = newBuildInfo
                            }
                            return artifactoryBuildInfo[server_id]
                          })
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
                          def stash_dir = workspace
                          if (meta.stash.containsKey('dir')) {
                            if (meta.stash.dir.startsWith('/')) {
                              stash_dir = meta.stash.dir
                            } else {
                              stash_dir = "${workspace}/${meta.stash.dir}"
                            }
                          }
                          // Make stash locations node-independent by making them relative to the Jenkins workspace
                          if (stash_dir.startsWith('/')) {
                            def cwd = steps.pwd()
                            // We could use java.io.File and java.nio.file.Path relativize, but that requires extra script approvals.
                            stash_dir = steps.sh(script: "realpath --relative-to=$cwd ${stash_dir}", returnStdout: true).trim()
                            if (stash_dir == '') {
                              stash_dir = '.'
                            }
                          }
                          steps.dir(stash_dir) {
                            steps.stash(params)
                          }
                          this.stashes[name] = [dir: stash_dir, nodes: [(steps.env.NODE_NAME): true]]
                        }
                        if (meta.containsKey('worktrees')) {
                          def name = "${phase}-${variant}-worktree-transfer.bundle"
                          steps.stash(
                              name: name,
                              includes: 'worktree-transfer.bundle',
                            )
                          this.worktree_bundles[name] = [nodes: [(steps.env.NODE_NAME): true]]
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
            this.on_build_node { cmd ->
              if (this.has_submittable_change()) {
                steps.stage('submit') {
                  this.with_credentials() {
                    // addBuildSteps(steps.isMainlineBranch(steps.env.CHANGE_TARGET) || steps.isReleaseBranch(steps.env.CHANGE_TARGET))
                    steps.sh(script: "${cmd} submit")
                  }
                }
              }
            }
          }
        }

        if (artifactoryBuildInfo) {
          assert this.nodes : "When we have artifactory build info we expect to have execution nodes that it got produced on"
          this.on_build_node { cmd ->
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
      } catch(Exception e) {
        if (this.change != null) {
          def buildStatus = (e.getClass() == org.jenkinsci.plugins.workflow.steps.FlowInterruptedException) ? 'ABORTED' : 'FAILURE'
          this.change.notify_build_result(
              get_job_name(), steps.env.CHANGE_BRANCH, this.source_commit, buildStatus)
          this.change.notify_build_result(
              get_job_name(), steps.env.CHANGE_TARGET, steps.env.GIT_COMMIT, buildStatus)
        }
        throw e
      }

      if (this.change != null) {
        this.change.notify_build_result(get_job_name(), steps.env.CHANGE_BRANCH, this.source_commit, steps.currentBuild.result ?: 'SUCCESS')
        this.change.notify_build_result(get_job_name(), steps.env.CHANGE_TARGET, steps.env.GIT_COMMIT, steps.currentBuild.result ?: 'SUCCESS')
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
