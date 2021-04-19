/*
 * Copyright (c) 2019 - 2020 TomTom N.V.
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

def version = 'release/1'
@NonCPS
String transform_ssh_to_https(String url)
{
  // Rewrite SSH URLs to HTTP URLs, assuming that we don't need authentication
  def m = url =~ /^ssh:\/\/(?:\w+@)?(\w+(?:\.\w+)*\.?)(?::\d+)?\/(.+)$/
  if (!m && !(url =~ /^\w+:\/\/.*/)) {
    m = url =~ /^(?:\w+@)?(\w+(?:\.\w+)*\.?):(.+)$/
  }
  m.each { match ->
    url = "https://${match[1]}/scm/${match[2]}"
  }
  return url
}
def repo = transform_ssh_to_https(scm.userRemoteConfigs[0].url.split('/')[0..-2].join('/') + '/hopic.git')

library(
    identifier: "hopic@${version}",
    retriever: modernSCM([
        $class: 'GitSCMSource',
        remote: repo
  ]))
def hopic = getCiDriver("git+${repo}@${version}")

properties([
  pipelineTriggers([
    parameterizedCron(''
      + (BRANCH_NAME =~ /^master$|^release\/\d+(?:\.\d+)?$/ ? '''
        # trigger build as AUTO_MERGE every 2 hours during business hours on weekdays, on master and release branches only
        H H(7-20)/2 * * 1-5 % MODALITY=AUTO_MERGE
        ''' : '')
      + (BRANCH_NAME =~ /^release\/\d+(?:\.\d+)?$/ ? '''
        # Bump the version early on every Monday. Only does something if there are any bumpable changes since the last tagged version.
        H H(7-13) * * 1 % MODALITY=BUMP_VERSION
        ''' : '')
      ),
  ]),
  parameters([
    choice(name: 'MODALITY',
           choices: 'NORMAL\nAUTO_MERGE'
             + (BRANCH_NAME =~ /^release\/\d+(?:\.\d+)?$/ ? '\nBUMP_VERSION' : ''),
           description: 'Modality of this execution of the pipeline.'),
  ]),
])

timeout(time: 20, unit: 'MINUTES') {
  hopic.build(
    clean: params.CLEAN || params.MODALITY != "NORMAL",
  )
}
