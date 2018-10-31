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
  private venv
  private prerequisites_installed

  CiDriver(steps, repo, workspace) {
    this.repo = repo
    this.steps = steps
    this.venv = steps.pwd(tmp: true) + "/cidriver-venv"
    this.cmd = "${venv}/bin/python ${venv}/bin/ci-driver --config=\"${workspace}/cfg.yml\" --workspace=\"${workspace}\""
    this.prerequisites_installed = false
  }

  public def install_prerequisites() {
    if (!this.prerequisites_installed) {
      steps.sh(script: "pip install --user virtualenv\n"
                     + "~/.local/bin/virtualenv ${venv}\n"
                     + "${venv}/bin/python ${venv}/bin/easy_install pip\n"
                     + "${venv}/bin/python ${venv}/bin/pip install \"${this.repo}\"")
      this.prerequisites_installed = true
    }
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
              steps.stage("${phase}-${variant}") {
                steps.sh(script: "${this.cmd} build --phase=\"${phase}\" --variant=\"${variant}\"")
                if (phase == 'upload')
                {
                  def meta = steps.readJSON(text: steps.sh(
                      script: "${this.cmd} getinfo --phase=\"${phase}\" --variant=\"${variant}\"",
                      returnStdout: true,
                    ))
                  if (meta.containsKey('ivy-output-dir')) {
                    steps.stashPublishedArtifactsFiles(variant, meta['ivy-output-dir'])
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
  return new CiDriver(this, repo, WORKSPACE)
}
