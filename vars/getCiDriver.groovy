/*
 * Copyright (c) 2018 - 2021 TomTom N.V.
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
import hudson.model.ParametersDefinitionProperty
import org.jenkinsci.plugins.credentialsbinding.impl.CredentialNotFoundException
import org.jenkinsci.plugins.scriptsecurity.sandbox.RejectedAccessException
import org.jenkinsci.plugins.workflow.job.properties.DisableConcurrentBuildsJobProperty

class ChangeRequest {
  protected steps

  ChangeRequest(steps) {
    this.steps = steps
  }

  protected String shell_quote(word) {
    return "'" + (word as String).replace("'", "'\\''") + "'"
  }

  protected ArrayList line_split(String text) {
    return text.split('\\r?\\n') as ArrayList
  }

  protected boolean maySubmitImpl(String target_commit, String source_commit, boolean allow_cache = true) {
    return !line_split(steps.sh(script: 'LC_ALL=C.UTF-8 TZ=UTC git log ' + shell_quote(target_commit) + '..' + shell_quote(source_commit) + " --pretty='%H:%s' --reverse",
                                label: 'Hopic (internal): retrieving git log',
                                returnStdout: true)
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

  public boolean maySubmit(String target_commit, String source_commit, boolean allow_cache = true) {
    return this.maySubmitImpl(target_commit, source_commit, allow_cache)
  }

  public void abort_if_changed(String source_remote) {
  }

  public Map getinfo(String cmd) {
    return [:]
  }

  public Map apply(String cmd, String source_remote) {
    assert false : "Change request instance does not override apply()"
  }

  public void notify_build_result(String job_name, String branch, String commit, String result, boolean exclude_branches_filled_with_pr_branch_discovery) {
    // Default NOP
  }
}

class BitbucketPullRequest extends ChangeRequest {
  private url
  private info = null
  private credentialsId
  private refspec
  private restUrl = null
  private baseRestUrl = null
  private keyIds = [:]
  private source_commit = null

  BitbucketPullRequest(steps, url, credentialsId, refspec) {
    super(steps)
    this.url = url
    this.credentialsId = credentialsId
    this.refspec = refspec

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

  @Override
  public boolean maySubmit(String target_commit, String source_commit, boolean allow_cache = true) {
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

  private def current_source_commit(String source_remote) {
    assert steps.env.NODE_NAME != null, "current_source_commit must be executed on a node"
    def (remote_ref, local_ref) = this.refspec.tokenize(':')
    if (remote_ref.startsWith('+'))
      remote_ref = remote_ref.substring(1)

    def refs = line_split(
      steps.sh(
        script: "git ls-remote ${shell_quote(source_remote)}",
        label: 'Hopic: finding last commit of PR',
        returnStdout: true,
      )
    ).collectEntries { line ->
      def (hash, ref) = line.split('\t')
      [(ref): hash]
    }
    return refs[remote_ref] ?: refs["refs/heads/${remote_ref}"] ?: refs["refs/tags/${remote_ref}"]
  }

  @Override
  public void abort_if_changed(String source_remote) {
    if (this.source_commit == null)
      return

    final current_commit = this.current_source_commit(source_remote)
    if (this.source_commit != current_commit) {
      steps.currentBuild.result = 'ABORTED'
      steps.currentBuild.description = 'Aborted: build outdated; change request updated since start'
      steps.error("this build is outdated. Its change request got updated to ${current_commit} (from ${this.source_commit}).")
    }

    if (!this.info
      // we don't care about builds that weren't going to be merged anyway
     || !this.info.canMerge)
      return

    final old_cr_info = this.info
    def cur_cr_info = this.get_info(/* allow_cache=*/ false)
    // keep the cache intact as it's used to generate merge commit messages
    this.info = old_cr_info

    // Ignore the current INPROGRESS build from the merge vetoes
    for (int i = cur_cr_info.getOrDefault('vetoes', []).size() - 1; i >= 0; i--) {
      if (cur_cr_info.vetoes[i].summaryMessage == 'Not all required builds are successful yet') {
        if (!cur_cr_info.canMerge
         && cur_cr_info.vetoes.size() == 1) {
          cur_cr_info.canMerge = true
        }
        cur_cr_info.vetoes.remove(i)
        break
      }
    }

    String msg = ''
    if (!cur_cr_info.canMerge) {
      msg += '\n\033[33m[warning] no longer submitting because the BitBucket merge criteria are no longer met\033[39m'
      if (cur_cr_info.vetoes) {
        msg += '\n\033[36m[info] the following merge condition(s) are not met:'
        cur_cr_info.vetoes.each { veto ->
          if (veto.summaryMessage) {
            msg += "\n[info] summary: ${veto.summaryMessage}"
            if (veto.detailedMessage) {
              msg += "\n[info]   details: ${veto.detailedMessage}"
            }
          }
        }
        msg += '\033[39m'
      }
    }
    final String old_title = old_cr_info.getOrDefault('title', steps.env.CHANGE_TITLE)
    final String cur_title = cur_cr_info.getOrDefault('title', steps.env.CHANGE_TITLE)
    if (cur_title.trim() != old_title.trim()) {
      msg += '\n\033[33m[warning] no longer submitting because the change request\'s title changed\033[39m'
      msg += "\n\033[36m[info] old title: '${old_title}'"
      msg +=         "\n[info] new title: '${cur_title}'\033[39m"
    }
    final String old_description = old_cr_info.containsKey('description') ? old_cr_info.description.trim() : null
    final String cur_description = cur_cr_info.containsKey('description') ? cur_cr_info.description.trim() : null
    if (cur_description != old_description) {
      msg += '\n\033[33m[warning] no longer submitting because the change request\'s description changed\033[39m'
      msg += '\n\033[36m[info] old description:'
      if (old_description == null) {
        msg += ' null'
      } else {
        line_split(old_description).each { line ->
          msg += "\n[info]     ${line}"
        }
      }
      msg += '\n[info] new description:'
      if (cur_description == null) {
        msg += ' null'
      } else {
        line_split(cur_description).each { line ->
          msg += "\n[info]     ${line}"
        }
      }
      msg += '\033[39m'
    }

    // trim() that doesn't strip \033
    while (msg && msg[0] == '\n')
      msg = msg[1..-1]

    if (msg) {
      steps.println(msg)
      steps.currentBuild.result = 'ABORTED'
      if (!cur_cr_info.canMerge) {
        steps.currentBuild.description = "No longer submitting: Bitbucket merge criteria no longer met"
        steps.error("This build is outdated. Merge criteria of its change request are no longer met.")
      } else {
        steps.currentBuild.description = "No longer submitting: change request's metadata changed since start"
        steps.error("This build is outdated. Metadata of its change request changed.")
      }
    }
  }

  @Override
  public Map apply(String cmd, String source_remote) {
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

    if (this.source_commit == null) {
      // Pin to the head commit of the PR to ensure every node builds the same version, even when the PR gets updated while the build runs
      this.source_commit = this.current_source_commit(source_remote)
    }
    def cr_author = change_request.getOrDefault('author', [:]).getOrDefault('user', [:])
    def output = line_split(steps.sh(script: cmd
                                + ' prepare-source-tree'
                                + ' --author-name=' + shell_quote(cr_author.getOrDefault('displayName', steps.env.CHANGE_AUTHOR))
                                + ' --author-email=' + shell_quote(cr_author.getOrDefault('emailAddress', steps.env.CHANGE_AUTHOR_EMAIL))
                                + ' --author-date=' + shell_quote(String.format("@%.3f", change_request.author_time))
                                + ' --commit-date=' + shell_quote(String.format("@%.3f", change_request.commit_time))
                                + ' merge-change-request'
                                + ' --source-remote=' + shell_quote(source_remote)
                                + ' --source-ref=' + shell_quote(this.source_commit)
                                + ' --change-request=' + shell_quote(change_request.getOrDefault('id', steps.env.CHANGE_ID))
                                + ' --title=' + shell_quote(change_request.getOrDefault('title', steps.env.CHANGE_TITLE))
                                + extra_params,
                          label: 'Hopic: preparing source tree',
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

  @Override
  public void notify_build_result(String job_name, String branch, String commit, String result, boolean exclude_branches_filled_with_pr_branch_discovery) {
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

    // It is impossible to get this Bitbucket branch plugin trait setting via groovy, therefore it is a parameter here
    if (!exclude_branches_filled_with_pr_branch_discovery) {
      branch = "${steps.env.JOB_BASE_NAME}"
    }
    def key = "${job_name}/${branch}"

    if (!this.keyIds[key]) {
      // We could use java.security.MessageDigest instead of relying on a node. But that requires extra script approvals.
      assert steps.env.NODE_NAME != null, "notify_build_result must be executed on a node the first time"
      this.keyIds[key] = steps.sh(script: "echo -n ${shell_quote(key)} | md5sum",
                                  label: 'Hopic (internal): generating unique build key',
                                  returnStdout: true).substring(0, 32)
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
  private info = null

  ModalityRequest(steps, modality) {
    super(steps)
    this.modality = modality
  }

  @Override
  public Map getinfo(String cmd) {
    if (this.info == null) {
      this.info = steps.readJSON(text: steps.sh(
        script: "${cmd} getinfo --modality=${shell_quote(modality)}",
        label: "Hopic: retrieving configuration for modality '${modality}'",
        returnStdout: true,
      ))
    }
    return this.info
  }

  @Override
  public Map apply(String cmd, String source_remote) {
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
                            label: 'Hopic: preparing modality change to ' + modality,
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

class NodeExecution {
  String allocation_group
  String exec_name
  long end_time     // unix epoch time (in ms)
  long request_time // unix epoch time (in ms)
  long start_time   // unix epoch time (in ms)
  String status
}

class LockWaitingTime {
  String lock_name
  Long acquire_time // unix epoch time (in ms) (can be null)
  long release_time // unix epoch time (in ms)
  long request_time // unix epoch time (in ms)
}

class CiDriver {
  private repo
  private steps
  private base_cmds          = [:]
  private cmds               = [:]
  private nodes              = [:]
  private checkouts          = [:]
  private scm                = [:]
  private stashes            = [:]
  private worktree_bundles   = [:]
  private submit_info        = [:]
  private change             = null
  private source_commit      = "HEAD"
  private target_commit      = null
  private may_submit_result  = null
  private may_publish_result = null
  private pip_constraints    = null
  private virtualenvs        = [:]
  private config_file
  private bitbucket_api_credential_id  = null
  private LinkedHashMap<String, LinkedHashMap<Integer, NodeExecution[]>> nodes_usage = [:]
  private ArrayList<LockWaitingTime> lock_times = []
  private printMetrics
  private config_file_content = null

  private final default_node_expr = "Linux && Docker"

  CiDriver(Map params = [:], steps, String repo, printMetrics) {
    this.repo = repo
    this.steps = steps
    this.change = params.change
    this.config_file_content = params.config_file_content
    if (params.config_file_content && params.config) {
      steps.println("WARNING: ignoring config_file as config content has been provided")
    }
    this.config_file = params.config_file_content ? 'hopic-internal-config.yaml' : params.config
    this.bitbucket_api_credential_id = params.getOrDefault('bb_api_cred_id', 'tt_service_account_creds')
    this.scm = [
      credentialsId: steps.scm.userRemoteConfigs[0].credentialsId,
      refspec: steps.scm.userRemoteConfigs[0].refspec,
      url: steps.scm.userRemoteConfigs[0].url,
    ]
    this.printMetrics = printMetrics
  }

  private def get_change() {
    if (this.change == null) {
      if (steps.env.CHANGE_URL != null
       && steps.env.CHANGE_URL.contains('/pull-requests/'))
      {
        def httpServiceCredential = this.scm.credentialsId
        try {
          steps.withCredentials([steps.usernamePassword(
              credentialsId: httpServiceCredential,
              usernameVariable: 'USERNAME',
              passwordVariable: 'PASSWORD',
              )]) {
          }
        } catch (CredentialNotFoundException e1) {
          /* Fall back when this credential isn't usable for HTTP(S) Basic Auth */
          httpServiceCredential = this.bitbucket_api_credential_id
        }
        this.change = new BitbucketPullRequest(steps, steps.env.CHANGE_URL, httpServiceCredential, this.scm.refspec)
      }
      // FIXME: Don't rely on hard-coded build parameter, externalize this instead.
      else if (steps.env.MODALITY != null && steps.env.MODALITY != "NORMAL")
      {
        this.change = new ModalityRequest(steps, steps.env.MODALITY)
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

  private int get_number_of_executors() {
    try {
      return Jenkins.instance.getComputer(steps.env.NODE_NAME).numExecutors
    } catch(org.jenkinsci.plugins.scriptsecurity.sandbox.RejectedAccessException e) {
      steps.println('\033[33m[warning] could not determine number of executors because of missing script approval; '
                  + 'assuming one executor\033[39m')
      return 1
    }
  }

  private String get_executor_identifier(String variant = null) {
    if (variant && get_number_of_executors() > 1) {
      return "${steps.env.NODE_NAME}_${variant}"
    } else {
      return steps.env.NODE_NAME
    }
  }

  public def with_hopic(String variant = null, closure) {
    assert steps.env.NODE_NAME != null, "with_hopic must be executed on a node"

    String executor_identifier = get_executor_identifier(variant)
    if (!this.base_cmds.containsKey(executor_identifier)) {
      def venv = steps.pwd(tmp: true) + "/hopic-venv"
      def workspace = steps.pwd()
      // Timeout prevents infinite downloads from blocking the build forever
      steps.timeout(time: 1, unit: 'MINUTES', activity: true) {
        // Use the exact same Hopic version on every build node
        if (this.repo.startsWith("git+") && !(this.repo ==~ /.*@[0-9a-fA-F]{40}/)) {
          // Split on the last '@' only
          def split = this.repo[4..-1].split('@')
          def (remote, ref) = [split[0..-2].join('@'), split[-1]]
          def commit = line_split(steps.sh(script: "git ls-remote ${shell_quote(remote)}",
                                           label: 'Hopic (internal): finding latest Hopic commit',
                                           returnStdout: true)).find { line ->
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
TZ=UTC
export LC_ALL TZ
rm -rf ${shell_quote(venv)}
python3 -m virtualenv --clear ${shell_quote(venv)}
cd /
${shell_quote(venv)}/bin/python -m pip install --prefer-binary --upgrade ${shell_quote("pip>=21.1")} ${shell_quote(this.repo)}
""",
                 label: 'Hopic: installing Hopic')
      }

      def cmd = 'LC_ALL=C.UTF-8 TZ=UTC ' + shell_quote("${venv}/bin/python") + ' ' + shell_quote("${venv}/bin/hopic") + ' --color=always'
      if (this.config_file != null) {
        cmd += ' --workspace=' + shell_quote(workspace)
        def cfg_file = this.config_file
        if (this.config_file_content) {
          cfg_file = steps.pwd(tmp: true) + "/${this.config_file}"
          steps.writeFile(
            file: cfg_file,
            text: this.config_file_content
          )
        }
        def config_file_path = shell_quote(cfg_file.startsWith('/') ? cfg_file : "${workspace}/${cfg_file}")
        cmd += ' --config=' + "${config_file_path}"
      }
      this.base_cmds[executor_identifier] = cmd
      this.virtualenvs[executor_identifier] = venv
    }

    def (build_name, build_identifier) = get_build_id()
    def environment = [
      "BUILD_NAME=${build_name}",
      "BUILD_NUMBER=${build_identifier}",
    ]
    try {
      environment.add("JENKINS_VERSION=${Jenkins.VERSION}")
    } catch (RejectedAccessException e) {
    }
    return steps.withEnv(environment) {
      return closure(this.base_cmds[executor_identifier], this.virtualenvs[executor_identifier])
    }
  }

  private def with_git_credentials(closure) {
    // Ensure
    try {
      steps.withCredentials([steps.usernamePassword(
          credentialsId: this.scm.credentialsId,
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
            steps.sh(script: 'chmod 700 "${GIT_ASKPASS}"',
                     label: 'Hopic (internal): mark helper script as executable')
            def r = closure()
            steps.sh(script: 'rm "${GIT_ASKPASS}"',
                     label: 'Hopic (internal): cleaning up')
            return r
          }
      }
    } catch (CredentialNotFoundException e1) {
      try {
        return this.with_credentials([[
          id: this.scm.credentialsId,
          type: 'ssh-key',
          'ssh-command-variable': 'GIT_SSH'
        ]]) {
          return steps.withEnv(["GIT_SSH_VARIANT=ssh"]) {
            return closure()
          }
        }
      } catch (CredentialNotFoundException e2) {
        // Ignore, hoping that we're dealing with a passwordless SSH credential stored at ~/.ssh/id_rsa
        return closure()
      }
    }
  }

  private def with_credentials(credentials, Closure closure) {
    def creds_info = credentials.collect({ currentCredential ->
      def credential_id = currentCredential['id']
      def type          = currentCredential['type']

      if (type == 'username-password') {
        def user_var = currentCredential['username-variable']
        def pass_var = currentCredential['password-variable']
        return [
          white_listed_vars: [
            user_var,
            pass_var,
          ],
          with_credentials: steps.usernamePassword(
            credentialsId: credential_id,
            usernameVariable: user_var,
            passwordVariable: pass_var,)
        ]
      } else if (type == 'file') {
        def file_var = currentCredential['filename-variable']
        return [
          white_listed_vars: [
            file_var,
          ],
          with_credentials: steps.file(
            credentialsId: credential_id,
            variable: file_var,)
        ]
      } else if (type == 'string') {
        def string_var = currentCredential['string-variable']
        return [
          white_listed_vars: [
            string_var,
          ],
          with_credentials: steps.string(
            credentialsId: credential_id,
            variable: string_var,)
        ]
      } else if (type == 'ssh-key') {
        def command_var = currentCredential['ssh-command-variable']

        // normalize id for use as part of environment variable name
        def normalized_id = credential_id.toUpperCase().replaceAll(/[^A-Z0-9_]/, '_')
        def keyfile_var = "KEYFILE_${normalized_id}"
        def username_var = "USERNAME_${normalized_id}"
        def passphrase_var = "PASSPHRASE_${normalized_id}"

        def tmpdir = steps.pwd(tmp: true)
        def askpass_program = "${tmpdir}/jenkins-${normalized_id}-ssh-askpass.sh"
        def ssh_program = "${tmpdir}/jenkins-${normalized_id}-ssh.sh"

        return [
          white_listed_vars: [
            command_var,
          ],
          with_credentials: steps.sshUserPrivateKey(
            credentialsId: credential_id,
            keyFileVariable: keyfile_var,
            usernameVariable: username_var,
            passphraseVariable: passphrase_var,),
          environment: [
            "${command_var}=${ssh_program}",
            "${keyfile_var}=",
            "${username_var}=",
            "${passphrase_var}="
          ],
          files: [
            (askpass_program): {
              steps.writeFile(
                  file: askpass_program,
                  text: '''\
#!/bin/sh
echo ''' + shell_quote(steps.env[passphrase_var] ?: '') + '''
''')
              steps.sh(script: "chmod 700 ${shell_quote(askpass_program)}",
                       label: 'Hopic (internal): mark helper script as executable')
            },
            (ssh_program): {
              steps.writeFile(
                  file: ssh_program,
                  text: '''\
#!/bin/sh
# On OpenSSH versions < 8.4 SSH_ASKPASS gets ignored if DISPLAY is not set,
# even when SSH_ASKPASS_REQUIRE=force.
if [ -z "${DISPLAY:-}" ]; then
  DISPLAY=:123.456
  export DISPLAY
fi
SSH_ASKPASS_REQUIRE=force SSH_ASKPASS='''
+ shell_quote(askpass_program)
+ ''' exec ssh -i '''
+ shell_quote(steps.env[keyfile_var])
+ (steps.env[username_var] != null ? ''' -l ''' + shell_quote(steps.env[username_var]) : '')
+ ''' -o StrictHostKeyChecking=no -o IdentitiesOnly=yes "$@"
''')
              steps.sh(script: "chmod 700 ${shell_quote(ssh_program)}",
                       label: 'Hopic (internal): mark helper script as executable')
            },
          ],
        ]
      }
    })

    if (creds_info.size() == 0) {
      return closure(creds_info)
    }

    def files = creds_info*.files.flatten().collectEntries{it ?: [:]}

    try {
      return steps.withCredentials(creds_info*.with_credentials) {
        files.each { file, write_file ->
          write_file()
        }
        def environment = creds_info*.environment.flatten().findAll{it}
        if (environment) {
          return steps.withEnv(environment) {
            return closure(creds_info)
          }
        } else {
          return closure(creds_info)
        }
      }
    }
    catch (CredentialNotFoundException e) {
      steps.println("\033[31m[error] credential '${credentials*.id}' does not exist or is not of type '${credentials*.type}'\033[39m")
      throw e
    } finally {
      if (files) {
        steps.sh(script: 'rm -f -- ' + files.collect{shell_quote(it.key)}.join(' '),
                 label: 'Hopic (internal): cleaning up')
      }
    }
  }

  private def subcommand_with_credentials(String cmd, String subcmd, credentials, String description) {
    this.with_credentials(credentials) { creds_info ->
      def white_listed_vars = creds_info*.white_listed_vars.flatten().findAll{it}
      steps.sh(script: cmd
        + white_listed_vars.collect{" --whitelisted-var=${shell_quote(it)}"}.join('')
        + ' ' + subcmd,
        label: description)
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

    params += ' --target-remote=' + shell_quote(this.scm.url)
    params += ' --target-ref='    + shell_quote(target_ref)
    if (this.target_commit) {
      params += ' --target-commit=' + shell_quote(this.target_commit)
    }

    steps.env.GIT_COMMIT = this.with_git_credentials() {
      this.target_commit = steps.sh(script: cmd
                                          + ' checkout-source-tree'
                                          + params,
                                    label: 'Hopic: checking out source tree',
                                    returnStdout: true).trim()
      if (this.get_change() != null) {
        def meta = this.get_change().getinfo(cmd)
        def maybe_timeout = { Closure closure ->
          if (meta.containsKey('timeout')) {
            return steps.timeout(time: meta['timeout'], unit: 'SECONDS') {
              return closure()
            }
          }
          return closure()
        }

        def submit_info = this.with_credentials(meta.getOrDefault('with-credentials', [])) { creds_info ->
          maybe_timeout {
            def white_listed_vars = creds_info*.white_listed_vars.flatten().findAll{it}
            this.get_change().apply(
              cmd + white_listed_vars.collect{" --whitelisted-var=${shell_quote(it)}"}.join(''),
              this.scm.url,
            )
          }
        }
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
        } else if (this.submit_info && submit_info.commit != this.submit_info.commit) {
          steps.currentBuild.result = 'ABORTED'
          steps.currentBuild.description = "Aborted: applied change resulted in different HEAD"
          steps.error("""HEAD commit (${submit_info.commit}) does not match initial HEAD commit (${this.submit_info.commit}) of this build. Aborting build!""")
        }
        this.submit_info = submit_info
        return submit_info.commit
      }
      return this.target_commit
    }

    // Ensure any required extensions are available
    def install_extensions_param = ""
    if (this.pip_constraints) {
      def pip_constraints_file = tmpdir + '/pip-constraints.txt'
      steps.writeFile(
          file: pip_constraints_file,
          text: pip_constraints,
      )
      install_extensions_param = "--constraints ${shell_quote(pip_constraints_file)}"
    }
    steps.sh(script: "${cmd} install-extensions ${install_extensions_param}", label: 'Hopic: installing extensions')

    def code_dir_output = tmpdir + '/code-dir.txt'
    if (steps.sh(script: 'LC_ALL=C.UTF-8 TZ=UTC git config --get hopic.code.dir > ' + shell_quote(code_dir_output), returnStatus: true,
                 label: 'Hopic (internal): retrieving Hopic workspace directory') == 0) {
      workspace = steps.readFile(code_dir_output).trim()
    }

    return workspace
  }

  public def get_submit_version() {
    return this.submit_info.version
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
            label: 'Hopic (internal): checking if changes may be published',
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
  private def ensure_checkout(String cmd, clean = false, String variant = null) {
    assert steps.env.NODE_NAME != null, "ensure_checkout must be executed on a node"
    String executor_identifier = get_executor_identifier(variant)

    if (!this.checkouts.containsKey(executor_identifier)) {
      this.checkouts[executor_identifier] = this.checkout(cmd, clean)
    }
    this.worktree_bundles.each { name, bundle ->
      if (bundle.nodes[executor_identifier]) {
        return
      }
      steps.unstash(name)
      steps.sh(
          script: "${cmd} unbundle-worktrees --bundle=worktree-transfer.bundle",
          label: 'Hopic (internal): unbundle worktrees'
        )
      this.worktree_bundles[name].nodes[executor_identifier] = true
    }
    return this.checkouts[executor_identifier]
  }

  private get_repo_name_and_branch(repo_name, branch = get_branch_name()) {
    return "${repo_name}/${branch}"
  }

  public static version_is_prerelease(final String version) {
    return version ==~ /^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-(?:[-0-9a-zA-Z]+(?:\.[-0-9a-zA-Z])*))(?:\+(?:[-0-9a-zA-Z]+(?:\.[-0-9a-zA-Z])*))?$/
  }

  private boolean is_new_version() {
    def version = this.get_submit_version()
    if (version != null && CiDriver.version_is_prerelease(version)) {
      // Pre-release versions are not new versions
      return false
    }
    return true
  }

  private Map get_ci_locks(cmd, is_publishable_change) {
    def locks = ['global': [], 'from-phase': [:]]
    if (!is_publishable_change) {
      return locks
    } else {
      locks['global'].push(this.get_lock_name())
    }
    def config = steps.readJSON(text: steps.sh(
      script: "${cmd} show-config",
      label: 'Hopic (internal): retrieving additional CI lock names',
      returnStdout: true,
    ))
    def all_locks = config.getOrDefault('ci-locks', []).findAll { lock ->
      if (lock['lock-on-change'] == 'always' || 
        (lock['lock-on-change'] == 'new-version-only' && this.is_new_version())) {
          return true
        }
        return false
    }

    all_locks.each { lock ->
      def lock_name = get_repo_name_and_branch(lock['repo-name'], lock['branch'])
      if (lock.containsKey('from-phase-onward')) {
        locks['from-phase'].get(lock['from-phase-onward'], []).push(lock_name)
      } else {
        locks['global'].push(lock_name)
      }
    }
    return locks
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
    def repo_url  = this.scm.url
    def repo_name = repo_url.tokenize('/')[-2..-1].join('/') - ~/\.git$/ // "${project}/${repo}"
    get_repo_name_and_branch(repo_name)
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

  public Map<String, Map<Integer, NodeExecution[]>> get_node_allocations() {
    return this.nodes_usage
  }


  public AbstractList<LockWaitingTime> get_lock_metrics() {
    return this.lock_times
  }

  /**
   * Unstash everything previously stashed on other nodes that we didn't yet unstash here.
   *
   * @pre this has to be executed on a node
   */
  private def ensure_unstashed(String variant = null) {
    assert steps.env.NODE_NAME != null, "ensure_unstashed must be executed on a node"

    String executor_identifier = get_executor_identifier(variant)

    this.stashes.each { name, stash ->
      if (stash.nodes[executor_identifier]) {
        return
      }
      steps.dir(stash.dir) {
        steps.unstash(name)
      }
      this.stashes[name].nodes[executor_identifier] = true
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
              allowEmptyArchive: meta.archive['allow-missing']
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

  public def on_build_node(Map params = [:], Closure closure) {
    def node_expr = (
           params.node_expr
        ?: this.nodes.collect { variant, node -> node }.join(" || ")
        ?: params.getOrDefault('default_node_expr', this.default_node_expr)
      )

    return this.on_node([node_expr: node_expr, exec_name: params.name]) {
      return this.with_hopic { cmd ->
        this.ensure_checkout(cmd, params.getOrDefault('clean', false))
        this.ensure_unstashed()
        return closure(cmd)
      }
    }
  }

  private def with_workspace_for_variant(String variant, Closure closure) {
    if (get_number_of_executors() > 1) {
      /*
       * If the node has more than one executor, unfortunately, we'll need to manually handle workspaces,
       * as Jenkins has no means of requesting specific executors and workspaces on a node.
       */
      steps.println('\033[36m[info] node has multiple executors; Hopic will manage workspaces\033[39m')

      String target_identifier = (steps.env.CHANGE_TARGET ? "PR-${steps.env.CHANGE_ID}" : get_branch_name())
      String workspace_spec = "${get_job_name()}_${target_identifier}_${variant}"
      steps.ws(workspace_spec) {
        /* We need to be somewhat paranoid, as `steps.ws` is not guaranteed to give us the path we expect */
        String pwd = steps.pwd().replaceAll(/(\/|\\)+$/, "") // Strip any trailing slashes/backslashes
        assert pwd.endsWith(workspace_spec) :
               "Jenkins did not yield the correct workspace path (" + steps.pwd() + "), try rebuilding"

        return closure()
      }
    } else {
      return closure()
    }
  }

  private String determine_error_build_result(Exception e) {
    return e.getClass() == org.jenkinsci.plugins.workflow.steps.FlowInterruptedException ? 'ABORTED' : 'FAILURE'
  }

  private long get_unix_epoch_time() {
    return System.currentTimeMillis()
  }

  private def on_node(Map node_params = [:], Closure closure) {
    def node_expr = node_params.getOrDefault("node_expr", this.default_node_expr)
    def exec_name = node_params.getOrDefault("exec_name", "no execution name")
    def allocation_group = node_params.getOrDefault("allocation_group", exec_name)
    def request_time = this.get_unix_epoch_time()
    return steps.node(node_expr) {
      if (!this.nodes_usage.containsKey(steps.env.NODE_NAME)) {
        steps.sh(
          script: """
            echo network config for node ${steps.env.NODE_NAME} && 
            ((LC_ALL=C.UTF-8 ifconfig || LC_ALL=C.UTF-8 ip addr show) | grep inet) || echo -e '\\033[33m[warning] could not get ip information of the node' >&2
          """,
          label: 'Hopic (internal): node ip logging')
      }
      NodeExecution usage_entry
      if (exec_name != null) {
        usage_entry = new NodeExecution(allocation_group: allocation_group, exec_name: exec_name, request_time: request_time, start_time: this.get_unix_epoch_time())
        this.nodes_usage.get(steps.env.NODE_NAME, [:]).get(steps.env.EXECUTOR_NUMBER as Integer, []).add(usage_entry)
      }
      def build_result = 'SUCCESS'
      try {
        return closure()
      } catch(Exception e) {
        build_result = this.determine_error_build_result(e)
        throw e
      } finally {
        if (exec_name != null) {
          assert usage_entry != null
          assert usage_entry.exec_name == exec_name
          usage_entry.end_time = this.get_unix_epoch_time()
          usage_entry.status = steps.currentBuild.currentResult != 'SUCCESS' ? steps.currentBuild.currentResult : build_result
        }
      }
    }
  }

  @NonCPS
  private def determine_props() {
    List props = null
    try {
      props = steps.currentBuild.rawBuild.parent.properties.collect { k, v -> v }

      def non_param_props = []
      def params = [:]
      props.each {
        if (it instanceof ParametersDefinitionProperty) {
          it.parameterDefinitions.each {
            params[it.name] = it
          }
        } else {
          non_param_props << it
        }
      }
      return [non_param_props, params]
    } catch (RejectedAccessException e) {
      return [props, null]
    }
  }

  private def extend_build_properties() {
    def (props, params) = determine_props()
    if (props == null) {
      steps.echo('\033[33m[warning] could not determine build properties, will not add extra properties\033[39m')
      return
    }

    if (!props.any { it instanceof DisableConcurrentBuildsJobProperty }) {
      props.add(steps.disableConcurrentBuilds())
    }

    if (params == null) {
      steps.echo('\033[33m[warning] could not determine build parameters, will not add extra parameters\033[39m')
    } else {
      if (!params.containsKey('HOPIC_VERBOSITY')) {
        params['HOPIC_VERBOSITY'] = steps.choice(
          name:        'HOPIC_VERBOSITY',
          description: 'Verbosity level to execute Hopic at.',
          choices:     ['INFO', 'DEBUG'],
        )
      }
      if (!params.containsKey('GIT_VERBOSITY')) {
        params['GIT_VERBOSITY'] = steps.choice(
          name:        'GIT_VERBOSITY',
          description: 'Verbosity level to execute Hopic\'s Git commands at.',
          choices:     ['INFO', 'DEBUG'],
        )
      }
      if (!params.containsKey('CLEAN')) {
        params['CLEAN'] = steps.booleanParam(
          name:        'CLEAN',
          description: 'Clean build',
          defaultValue: false,
        )
      }

      props.add(steps.parameters(params.values()))
    }
    steps.properties(props)
  }

  private def decorate_output(Closure closure) {
    steps.timestamps {
      steps.ansiColor('xterm') {
        if (steps.env.GIT_VERBOSITY != null
         && steps.env.GIT_VERBOSITY.toUpperCase() == 'DEBUG'
         && steps.env.GIT_PYTHON_TRACE == null) {
          return steps.withEnv(['GIT_PYTHON_TRACE=full']) {
            return closure()
          }
        } else {
          return closure()
        }
      }
    }
  }

  private void build_variant(String phase, String variant, String cmd, String workspace, Map artifactoryBuildInfo, String hopic_extra_arguments) {
    steps.stage("${phase}-${variant}") {
      // Interruption point (just after potentially lengthy node acquisition):
      // abort PR builds that got changed since the start of this build
      if (this.has_change()) {
        this.with_git_credentials() {
          this.get_change().abort_if_changed(this.scm.url)
        }
      }

      // Meta-data retrieval needs to take place on the executing node to ensure environment variable expansion happens properly
      def meta = steps.readJSON(text: steps.sh(
          script: "${cmd} getinfo --phase=" + shell_quote(phase) + ' --variant=' + shell_quote(variant),
          label: "Hopic: retrieving configuration for phase '${phase}', variant '${variant}'",
          returnStdout: true,
        ))

      def error_occurred = false
      def maybe_timeout = { Closure closure ->
        if (meta.containsKey('timeout')) {
          return steps.timeout(time: meta['timeout'], unit: 'SECONDS') {
            return closure()
          }
        }
        return closure()
      }
      try {
        maybe_timeout {
          this.subcommand_with_credentials(
              cmd + hopic_extra_arguments,
              'build'
            + ' --phase=' + shell_quote(phase)
            + ' --variant=' + shell_quote(variant)
            , meta.getOrDefault('with-credentials', []),
            , "Hopic: running build for phase '" + phase + "',  variant '" + variant + "'"
            )
        }
      } catch(Exception e) {
        error_occurred = true // Jenkins only sets its currentResult to Failure after all user code is executed
        throw e
      } finally {
        this.archive_artifacts_if_enabled(meta, workspace, error_occurred) { server_id ->
          if (!artifactoryBuildInfo.containsKey(server_id)) {
            def newBuildInfo = steps.Artifactory.newBuildInfo()
            def (build_name, build_identifier) = get_build_id()
            newBuildInfo.name = build_name
            newBuildInfo.number = build_identifier
            artifactoryBuildInfo[server_id] = newBuildInfo
          }
          return artifactoryBuildInfo[server_id]
        }
        if (meta.containsKey('junit')) {
          steps.dir(workspace) {
            meta.junit['test-results'].each { result ->
              steps.junit(
                testResults: result,
                allowEmptyResults: meta.junit['allow-missing'])
            }
          }
        }
      }

      def executor_identifier = get_executor_identifier(variant)
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
          // This check, unlike relativize() below, doesn't depend on File() and thus doesn't require script approval
          if (stash_dir == cwd) {
            stash_dir = '.'
          } else {
            cwd = new File(cwd).toPath()
            stash_dir = cwd.relativize(new File(stash_dir).toPath()) as String
          }
          if (stash_dir == '') {
            stash_dir = '.'
          }
        }
        steps.dir(stash_dir) {
          steps.stash(params)
        }
        this.stashes[name] = [dir: stash_dir, nodes: [(executor_identifier): true]]
      }
      if (meta.containsKey('worktrees')) {
        def name = "${phase}-${variant}-worktree-transfer.bundle"
        steps.stash(
            name: name,
            includes: 'worktree-transfer.bundle',
          )
        this.worktree_bundles[name] = [nodes: [(executor_identifier): true]]
      }

      // Interruption point (just before node release potentially followed by lengthy node acquisition):
      // abort PR builds that got changed since the start of this build
      if (this.has_change()) {
        this.with_git_credentials() {
          this.get_change().abort_if_changed(this.scm.url)
        }
      }
    }
  }

  public def with_locks(List<String> lock_names) {
    return { closure ->
      if (lock_names.size()) {
        def lock_closure = { locked_closure ->
          if (lock_names.size() > 1) {
            steps.lock(resource: lock_names[0], extra: lock_names[1..-1].collect{['resource': it]}) {
              locked_closure()
            }
          } else {
            steps.lock(lock_names[0]) {
              locked_closure()
            }
          }
        }

        def acquire_time = null
        def lock_request_time = this.get_unix_epoch_time()
        try {
          return lock_closure {
            acquire_time = this.get_unix_epoch_time()
            return closure()
          }
        } finally {
          def lock_release_time = this.get_unix_epoch_time()
          lock_names.each {
            this.lock_times.add(new LockWaitingTime(lock_name: it, acquire_time: acquire_time, request_time: lock_request_time, release_time: lock_release_time))
          }
        }
      } else {
        // NOP as default 
        closure()
      }
    }
  }

  private def build_phases(phases, clean, artifactoryBuildInfo, hopic_extra_arguments, submit_meta, from_phases_locks, previous_phase_locks = []) {
    if (!phases) {
      return submit_if_needed(submit_meta, hopic_extra_arguments)
    }
    def build_phases_func = { locks ->
      build_phases(phases, clean, artifactoryBuildInfo, hopic_extra_arguments, submit_meta, from_phases_locks, locks ?: previous_phase_locks)
    }
    def is_build_successful = steps.currentBuild.currentResult == 'SUCCESS'
    final phase = phases.keySet().first()
    // Make sure steps exclusive to changes are skipped when a failure occurred during one of the previous phases.
    final variants = phases.remove(phase).findAll { variant, meta ->
      def run_on_change = meta.run_on_change
      if (run_on_change == 'only' || run_on_change == 'new-version-only') {
        // run_on_change variants should not be executed for unstable builds 
        if (!is_build_successful) {
          steps.println("Skipping variant ${variant} in ${phase} because build is not successful")
          return false
        }
      }
      return true
    }

    def current_phase_locks = is_build_successful ? from_phases_locks.getOrDefault(phase, []) + previous_phase_locks : []
    // Skip creation of a stage for phases with no variants to execute
    if (variants.size() != 0) {
      def lock_phase_onward_if_necessary = this.with_locks(current_phase_locks)
      lock_phase_onward_if_necessary {
        steps.stage(phase) {
          def stepsForBuilding = variants.collectEntries { variant, meta ->
            def label = meta.label
            [ (variant): {
              if (this.nodes.containsKey(variant)) {
                label = this.nodes[variant]
              }
              this.on_node(node_expr: label, exec_name: "${phase}-${variant}", allocation_group: phase) {
                with_workspace_for_variant(variant) {
                  this.with_hopic(variant) { cmd ->
                    // If working with multiple executors on this node, uniquely identify this node by variant
                    // to ensure the correct workspace.
                    final workspace = this.ensure_checkout(cmd, clean, variant)
                    this.pin_variant_to_current_node(variant)

                    this.ensure_unstashed(variant)

                    if (!meta.nop) {
                      this.build_variant(phase, variant, cmd, workspace, artifactoryBuildInfo, hopic_extra_arguments)
                    }

                    // Execute a string of uninterrupted phases with our current variant for which we don't need to wait on preceding phases
                    //
                    // Using a regular for loop because we need to break out of it early and .takeWhile doesn't work with closures defined in CPS context
                    for (next_phase in phases.keySet()) {
                      final next_variants = phases[next_phase]

                      if (!next_variants.containsKey(variant)
                      // comparing against 'false' directly because we want to reject 'null' too
                      || next_variants[variant].wait_on_full_previous_phase != false) {
                        break
                      }

                      // Prevent executing this variant again during the phase it really belongs too
                      final next_variant = next_variants.remove(variant)
                      assert next_variant.run_on_change == 'always'

                      // Execute this variant's next phase already.
                      // Because the user asked for it, in order not to relinquish this node until we really have to.
                      if (!next_variant.nop) {
                        this.build_variant(next_phase, variant, cmd, workspace, artifactoryBuildInfo, hopic_extra_arguments)
                      }
                    }
                  }
                }
              }
            }]
          }
          steps.parallel stepsForBuilding
        }
        build_phases_func()
      }
    } else {
      build_phases_func(current_phase_locks)
    }
  }

  private def submit_if_needed(submit_meta, hopic_extra_arguments) {
    if (this.may_submit_result != false) {
      this.on_build_node(node_expr: submit_meta['node-label'], name: 'submit') { cmd ->
        if (!this.has_submittable_change()) {
          // Prevent reporting 'submit' as having run as we didn't actually do anything
          def usage_entry = this.nodes_usage[steps.env.NODE_NAME][steps.env.EXECUTOR_NUMBER as Integer].pop()
          assert usage_entry.exec_name == 'submit'
          return
        }

        steps.stage('submit') {
          this.with_git_credentials() {
            this.get_change().abort_if_changed(this.scm.url)
            def maybe_timeout = { Closure closure ->
              if (submit_meta.containsKey('timeout')) {
                return steps.timeout(time: submit_meta['timeout'], unit: 'SECONDS') {
                  return closure()
                }
              }
              return closure()
            }
            maybe_timeout {
              this.subcommand_with_credentials(
                  cmd + hopic_extra_arguments,
                  'submit'
                , submit_meta.getOrDefault('with-credentials', []),
                'Hopic: submitting merge')
            }
          }
        }
      }
    }
  }

  public def build(Map buildParams = [:]) {
    def clean = buildParams.getOrDefault('clean', false)
    def default_node = buildParams.getOrDefault('default_node_expr', this.default_node_expr)
    def exclude_branches_filled_with_pr_branch_discovery = buildParams.getOrDefault('exclude_branches_filled_with_pr_branch_discovery', true)

    this.extend_build_properties()
    this.decorate_output {
      def (phases, is_publishable_change, submit_meta, locks) = this.on_node(node_expr: default_node, exec_name: "hopic-init") {
        return this.with_hopic { cmd, venv ->
          def workspace = steps.pwd()

          /*
           * We're splitting the enumeration of phases and variants from their execution in order to
           * enable Jenkins to execute the different variants within a phase in parallel.
           *
           * In order to do this we only check out the CI config file to the orchestrator node.
           */
          def scm = steps.checkout(steps.scm)

          // Don't trust Jenkin's scm.GIT_COMMIT because it sometimes lies
          steps.env.GIT_COMMIT          = steps.sh(script: 'LC_ALL=C.UTF-8 TZ=UTC git rev-parse HEAD',
                                                   label: 'Hopic (internal): determine current commit (because Jenkins lies!)',
                                                   returnStdout: true).trim()
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

          // Pin the currently installed pip packages to ensure all variants use the same templates
          this.pip_constraints = steps.sh(
              script: "${venv}/bin/python -m pip freeze",
              label: "Get list of installed pip packages",
              returnStdout: true,
          ).replaceAll(/(?m)^[A-Za-z0-9-_.]+ *@.+$/, "") // Remove any URL constraints, as adding support for those has proven troublesome

          def phases = steps.readJSON(text: steps.sh(
              script: "${cmd} getinfo",
              label: 'Hopic: retrieving execution graph',
              returnStdout: true,
            )).collectEntries { phase, variants ->
            [
              (phase): variants.collectEntries { variant, meta ->
                [
                  (variant): [
                    label: meta.getOrDefault('node-label', default_node),
                    nop: meta.getOrDefault('nop', false),
                    run_on_change: meta.getOrDefault('run-on-change', 'always'),
                    wait_on_full_previous_phase: meta.getOrDefault('wait-on-full-previous-phase', true),
                  ]
                ]
              }
            ]
          }

          def submit_meta = steps.readJSON(text: steps.sh(
              script: "${cmd} getinfo --post-submit",
              label: 'Hopic (internal): running post submit',
              returnStdout: true,
            ))

          def is_publishable = this.has_publishable_change()

          if (is_publishable) {
            // Ensure a new checkout is performed because the target repository may change while waiting for the lock
            final executor_identifier = get_executor_identifier()
            this.checkouts.remove(executor_identifier)
          }

          // Report start of build. _Must_ come after having determined whether this build is submittable and
          // publishable, because it may affect the result of the submittability check.
          if (this.change != null) {
            this.change.notify_build_result(get_job_name(), steps.env.CHANGE_BRANCH, this.source_commit, 'STARTING', exclude_branches_filled_with_pr_branch_discovery)
            this.change.notify_build_result(get_job_name(), steps.env.CHANGE_TARGET, steps.env.GIT_COMMIT, 'STARTING', exclude_branches_filled_with_pr_branch_discovery)
          }

          return [phases, is_publishable, submit_meta, get_ci_locks(cmd, is_publishable)]
        }
      }

      def lock_if_necessary = this.with_locks(locks.global)

      def artifactoryBuildInfo = [:]
      def hopic_extra_arguments = is_publishable_change ? ' --publishable-version': ''

      try {
        lock_if_necessary {
          phases = phases.collectEntries { phase, variants ->
            // Make sure steps exclusive to changes, or not intended to execute for changes, are skipped when appropriate
            [
              (phase): variants.findAll { variant, meta ->
                def run_on_change = meta.run_on_change

                if (run_on_change == 'always') {
                  return true
                } else if (run_on_change == 'never') {
                  return !this.has_change()
                } else if (run_on_change == 'only' || run_on_change == 'new-version-only') {
                  if (this.source_commit == null
                   || this.target_commit == null) {
                    // Don't have enough information to determine whether this is a submittable change: assume it is
                    return true
                  }
                  if (run_on_change == 'new-version-only' && !this.is_new_version()) {
                    return false
                  }
                  return is_publishable_change
                }
                assert false : "Unknown 'run-on-change' option: ${run_on_change}"
              },
            ]
          }
          // Clear the target commit hash and submit hash that we determined outside of 'lock_if_necessary' because the target branch
          // may have moved forward while we didn't hold the lock.
          this.target_commit = null
          this.submit_info = [:]
          this.build_phases(phases, clean, artifactoryBuildInfo, hopic_extra_arguments, submit_meta, locks['from-phase'])
        }

        if (artifactoryBuildInfo) {
          assert this.nodes : "When we have artifactory build info we expect to have execution nodes that it got produced on"
          this.on_build_node { cmd ->
            def config = steps.readJSON(text: steps.sh(
                script: "${cmd} show-config",
                label: 'Hopic (internal): determine Artifactory promotion configuration',
                returnStdout: true,
              ))

            artifactoryBuildInfo.each { server_id, buildInfo ->
              def promotion_config = config.getOrDefault('artifactory', [:]).getOrDefault('promotion', [:]).getOrDefault(server_id, [:])

              def server = steps.Artifactory.server server_id
              server.publishBuildInfo(buildInfo)
              if (promotion_config.containsKey('target-repo')
               && is_publishable_change) {
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
          def buildStatus = this.determine_error_build_result(e)
          this.change.notify_build_result(
              get_job_name(), steps.env.CHANGE_BRANCH, this.source_commit, buildStatus, exclude_branches_filled_with_pr_branch_discovery)
          this.change.notify_build_result(
              get_job_name(), steps.env.CHANGE_TARGET, steps.env.GIT_COMMIT, buildStatus, exclude_branches_filled_with_pr_branch_discovery)
        }
        throw e
      } finally {
        this.printMetrics.print_node_usage(this.nodes_usage)
        this.printMetrics.print_critical_path(this.nodes_usage)
      }

      if (this.change != null) {
        this.change.notify_build_result(get_job_name(), steps.env.CHANGE_BRANCH, this.source_commit, steps.currentBuild.result ?: 'SUCCESS', exclude_branches_filled_with_pr_branch_discovery)
        this.change.notify_build_result(get_job_name(), steps.env.CHANGE_TARGET, steps.env.GIT_COMMIT, steps.currentBuild.result ?: 'SUCCESS', exclude_branches_filled_with_pr_branch_discovery)
      }
    }
  }
}

/**
  * getCiDriver()
  */

def call(Map params = [:], String repo) {
  return new CiDriver(params, this, repo, printMetrics(this))
}
