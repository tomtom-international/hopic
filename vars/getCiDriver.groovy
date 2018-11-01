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
  private cmd
  private steps
  private nodes

  CiDriver(steps, repo) {
    this.repo = repo
    this.steps = steps
    this.nodes = [:]
  }

  public def install_prerequisites() {
    def venv = steps.pwd(tmp: true) + "/cidriver-venv"
    def workspace = steps.pwd()
    steps.sh(script: "python -m virtualenv --clear ${venv}\n"
                   + "${venv}/bin/python -m pip install \"${this.repo}\"")
    this.cmd = "${venv}/bin/python ${venv}/bin/ci-driver --config=\"${workspace}/cfg.yml\" --workspace=\"${workspace}\""
  }

  public def build() {
    this.install_prerequisites()

    /*
     * We're splitting the enumeration of phases and variants from their execution in order to
     * enable Jenkins to execute the different variants within a phase in parallel.
     */
    def phases = steps.sh(
        script: "${this.cmd} phases",
        returnStdout: true,
      ).split("\\r?\\n")

    phases.each { phase ->
        def variants = steps.sh(
            script: "${this.cmd} variants --phase=\"${phase}\"",
            returnStdout: true,
          ).split("\\r?\\n")
        steps.stage(phase) {
          def stepsForBuilding = variants.collectEntries { variant ->
            [ "${phase}-${variant}": {
              def meta = steps.readJSON(text: steps.sh(
                  script: "${this.cmd} getinfo --phase=\"${phase}\" --variant=\"${variant}\"",
                  returnStdout: true,
                ))
              def label = 'Linux && Docker'
              if (meta.containsKey('node-label')) {
                label = meta['node-label']
              }
              if (this.nodes.containsKey(variant)) {
                label = this.nodes[variant]
              }
              steps.node(label) {
                steps.stage("${phase}-${variant}") {
                  if (!this.nodes.containsKey(variant)) {
                    this.nodes[variant] = steps.env.NODE_NAME
                    this.install_prerequisites()
                    // TODO: checkout here
                  }
                  steps.sh(script: "${this.cmd} build --phase=\"${phase}\" --variant=\"${variant}\"")
                  if (phase == 'upload')
                  {
                    if (meta.containsKey('ivy-output-dir')) {
                      steps.stashPublishedArtifactsFiles(variant, meta['ivy-output-dir'])
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
