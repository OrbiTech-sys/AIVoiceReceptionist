#!/usr/bin/env bash
# Print the AWS Systems Manager Parameter Store parameters under a path as NAME=value lines.
#
#   fetch-ssm-env.sh [PATH]          PATH defaults to /voicerep/
#
# A parameter called /voicerep/GROQ_API_KEY becomes the line GROQ_API_KEY=<its value>. Only the last part of the
# name is used, so /voicerep/anything/GROQ_API_KEY works too. The output is meant for deploy/merge-env.sh.
#
# Needs the AWS CLI and credentials that may read the path (on EC2: an instance role with ssm:GetParametersByPath,
# see .github/workflows/deploy.yml). The region comes from AWS_REGION or AWS_DEFAULT_REGION.
# Never prints anything but the NAME=value lines on stdout; errors go to stderr.
set -euo pipefail

path=${1:-/voicerep/}

if ! command -v aws >/dev/null 2>&1; then
  echo "An error occurred (AwsCliNotInstalled): the aws command was not found" >&2
  exit 127
fi

args=(ssm get-parameters-by-path --path "$path" --recursive --with-decryption
      --query 'Parameters[].[Name,Value]' --output text)
region=${AWS_REGION:-${AWS_DEFAULT_REGION:-}}
if [ -n "$region" ]; then args+=(--region "$region"); fi

# "aws ... --output text" prints one parameter per line as NAME<TAB>VALUE.
aws "${args[@]}" | awk -F'\t' 'NF >= 2 {
  name = $1; sub(/.*\//, "", name)
  value = $0; sub(/^[^\t]*\t/, "", value)
  print name "=" value
}'
