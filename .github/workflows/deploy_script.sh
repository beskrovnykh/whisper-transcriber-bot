#!/bin/bash

# Extract the bot token from chalice config.json
get_bot_token() {
    local chalice_config_file=".chalice/config.json"
    if [ -f "$chalice_config_file" ]; then
        BOT_TOKEN=$(python -c "import json; f=open('$chalice_config_file', 'r'); config=json.load(f); f.close(); print(config['stages']['dev']['environment_variables']['TELEGRAM_BOT_ID'])")
        echo $BOT_TOKEN
    else
        echo "Error: Chalice config file not found."
        exit 1
    fi
}

# Set the Telegram bot webhook
set_webhook() {
    local api_url="$1"
    local webhook_url="${api_url}webhook"  # Assuming the webhook endpoint is at /webhook
    local bot_token=$(get_bot_token)

    # Set the webhook for your bot using the Telegram API
    curl -X POST "https://api.telegram.org/bot${bot_token}/setWebhook?url=${webhook_url}"
}

deploy_bot() {
    # Get the current working directory
    # local project_dir=$(pwd)

    # # Activate the virtual environment
    # if [ -f "$project_dir/.venv/bin/activate" ]; then
    #     source "$project_dir/.venv/bin/activate"
    # else
    #     echo "Error: Virtual environment does not exist in $project_dir. Please set up the project first."
    #     exit 1
    # fi

    # # Install the required dependencies
    # if [ -f "$project_dir/requirements.txt" ]; then
    #     pip install -r "$project_dir/requirements.txt"
    # else
    #     echo "Error: requirements.txt not found in $project_dir."
    #     exit 1
    # fi

    # Deploy the Chalice app
    DEPLOY_OUTPUT=$(chalice deploy --connection-timeout 600)
    echo "$DEPLOY_OUTPUT"
    API_URL=$(echo "$DEPLOY_OUTPUT" | grep "Rest API URL" | awk '{print $NF}')

    if [ -n "$API_URL" ]; then
        set_webhook "$API_URL"
    else
        echo "Error: Failed to extract the Rest API URL from deployment output."
        exit 1
    fi

    echo "Deployment and webhook setup completed!"
}

deploy_bot