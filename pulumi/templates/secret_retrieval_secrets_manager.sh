# Retrieve secrets from AWS Secrets Manager
REGION=$(ec2-metadata --availability-zone | cut -d " " -f 2 | sed 's/[a-z]$//')
PERMID_API_KEY=$(aws secretsmanager get-secret-value --secret-id {name_prefix}-permid-api-key --region $REGION --query SecretString --output text)
GEONAMES_USER=$(aws secretsmanager get-secret-value --secret-id {name_prefix}-geonames-user --region $REGION --query SecretString --output text)
