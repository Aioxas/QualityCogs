node {
    waitUntil {
        stage('Check PEP-8') {
            sh 'flake8 --max-line-length=120 --show-source .'
        }
    }
    step([$class: 'GitHubCommitStatusSetter', statusResultSource: [$class: 'ConditionalStatusResultSource', results: []]])
}
