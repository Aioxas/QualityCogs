node {
    step([$class: 'GitHubCommitStatusSetter'])
    stage('Check PEP-8') {
        sh 'flake8 --max-line-length=120 --show-source .'
    }
}
node {
    step([$class: 'GitHubCommitStatusSetter'])
}
