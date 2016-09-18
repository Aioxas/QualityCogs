node {
    step([$class: 'GitHubCommitStatusSetter'])
    stage('Check PEP-8') {
        sh 'flake8 --max-line-length=120 --show-source .'
    }
}
node {
    step([$class: 'GitHubCommitStatusSetter', errorHandlers: [[$class: 'ChangingBuildStatusErrorHandler']], statusResultSource: [$class: 'ConditionalStatusResultSource', results: [[$class: 'BetterThanOrEqualBuildResult', message: 'Build successful!', result: 'SUCCESS', state: 'SUCCESS'], [$class: 'BetterThanOrEqualBuildResult', message: 'Build failed!', result: 'FAILURE', state: 'FAILURE']]]])
}
