# Whisper Transcriber Bot

Whisper Transcriber Bot is a Telegram bot that leverages the OpenAI Whisper ASR API to transcribe audio from YouTube videos.

## Setup

Ensure Python 3.8 and pip are installed.

1. Clone the repository:

   ```
   git clone https://github.com/your-repo/whisper-transcriber-bot.git
   ```

2. Change into the project directory and setup a virtual environment:

   ```
   cd whisper-transcriber-bot
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

## Running Locally

1. Start the local server:

   ```
   chalice local
   ```

2. In a new terminal window, launch ngrok:

   ```
   ngrok http 8000
   ```

3. Copy the HTTPS URL from ngrok. Make a POST request to the `/set_webhook` endpoint with the ngrok URL as the 'webhook_url' in the request body. This can be done using tools like curl or Postman. This step is to set the webhook URL for the bot.

## Deployment

To deploy the bot on AWS, make sure you have AWS CLI configured with the necessary permissions. Then, deploy using Chalice:

   ```
   chalice deploy
   ```

Remember to update the webhook to the new API Gateway URL after deployment.
