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

  CiDriver(steps, repo) {
    this.cmds = [:]
    this.repo = repo
    this.steps = steps
    this.nodes = [:]
    this.workspaces = [:]
  }

  public def install_prerequisites() {
    if (!this.cmds.containsKey(steps.env.NODE_NAME)) {
      def venv = steps.pwd(tmp: true) + "/cidriver-venv"
      def workspace = steps.pwd()
      steps.sh(script: "python -m virtualenv --clear ${venv}\n"
                     + "${venv}/bin/python -m pip install \"${this.repo}\"")
      this.cmds[steps.env.NODE_NAME] = "${venv}/bin/python ${venv}/bin/ci-driver --config=\"${workspace}/cfg.yml\" --workspace=\"${workspace}\""
    }
    return this.cmds[steps.env.NODE_NAME]
  }

  private def checkout(clean = false) {
    this.install_prerequisites()

    def venv = steps.pwd(tmp: true) + "/cidriver-venv"
    def workspace = steps.pwd()
    def clean_param = clean ? " --clean" : ""
    def ref = steps.env.GIT_COMMIT
    if (steps.env.CHANGE_TARGET != null) {
      ref = steps.env.CHANGE_TARGET
    }
    steps.sh(script: "${venv}/bin/python ${venv}/bin/ci-driver --workspace=\"${workspace}\""
                     + " checkout-source-tree"
                     + " --target-remote=\"${steps.env.GIT_URL}\""
                     + " --target-ref=\"${ref}\""
                     + clean_param)
    return workspace
  }

  public def build(clean = false) {
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
        def variants = steps.sh(
            script: "${orchestrator_cmd} variants --phase=\"${phase}\"",
            returnStdout: true,
          ).split("\\r?\\n")
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
