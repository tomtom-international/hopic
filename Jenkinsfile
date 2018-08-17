pipeline {
  agent {
    node {
      label 'Linux && Docker'
    }
  }

  parameters {
    booleanParam(defaultValue: false, description: 'Clean build', name: 'CLEAN')
  }

  options {
    timestamps()
    disableConcurrentBuilds()
  }

  stages {
    stage("Commit Stage") {
      steps {
        script {
          def clean_param = ""
          if (params.CLEAN)
          {
            clean_param = "--clean"
          }

          def PROJECT_CFG = "somewhere/cfg.yml"
          def build_commit = sh(script: "ci-driver --config=${PROJECT_CFG} checkout-source-tree --target-remote=${GIT_URL} --target-ref=${BRANCH_NAME} ${clean_param}", returnStdout: true)
          def submit_commit = null
          if (BRANCH_NAME.startsWith('PR-')) {
            def pr = env.BRANCH_NAME.substring(env.BRANCH_NAME.indexOf('-') + 1)
            submit_commit = sh(script: "ci-driver --config=${PROJECT_CFG} prepare-source-tree --target-remote=${GIT_URL} --target-ref=${CHANGE_TARGET} --source-remote=${GIT_URL} --source-ref=${BRANCH_NAME} --pull-request-link=${PR_LINK}")
            build_commit = submit_commit
          }

          def build_phases = sh(script: "ci-driver --config ${PROJECT_CFG} build-phases --ref=${build_commit}", returnStdout: true).split("\r?\n")
          def stepsForBuilding = build_phases.collectEntries {
            ["Build ${it}" : {
              stage("${it}") {
                sh(script: "ci-driver --config ${PROJECT_CFG} build --ref=${build_commit} --phase=${it}")
              }
            }
          }
          parallel stepsForBuilding

          if (submit_commit != null) {
            sh(script: "ci-driver --config ${PROJECT_CFG} submit --target-remote=${GIT_URL} --target-ref=${CHANGE_TARGET} --ref=${submit_commit}")
          }
        }
      }
    }
  }
}
