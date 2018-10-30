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

/**
  * getCiDriver()
  *
  * @return string usable for interpolation in shell scripts as ci-driver command
  */

def call(version) {
  def cidriver = pwd(tmp: true) + "/cidriver-src"
  checkout(scm: [
      $class: 'GitSCM',
      userRemoteConfigs: [[
          url: 'https://bitbucket.example.com/scm/~muggenhor/cidriver.git',
          credentialsId: 'tt_service_account_creds',
        ]],
      branches: [[name: version]],
      browser: [
          $class: 'BitbucketWeb',
          repoUrl: 'https://bitbucket.example.com/users/muggenhor/repos/cidriver',
        ],
      extensions: [[
          $class: 'RelativeTargetDirectory',
          relativeTargetDir: cidriver,
        ]],
    ])
  return "PYTHONPATH=\"${cidriver}\" python -m cidriver"
}
