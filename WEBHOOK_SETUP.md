# DAS Webhook & Twilio WhatsApp Setup Guide

This guide explains how to set up the DAS signal webhook endpoint and configure Twilio for WhatsApp notifications.

## Overview

The backend now includes a public webhook endpoint (`/webhook/das`) that receives trading signals from DAS scripts and sends WhatsApp notifications via Twilio.

## 1. Install Dependencies

Install the Twilio SDK:

```bash
pip install -r requirements.txt
```

## 2. Twilio Setup

### Step 1: Create a Twilio Account
1. Go to https://www.twilio.com/ and sign up for a free account
2. Verify your phone number
3. Get your Account SID and Auth Token from the Twilio Console Dashboard

### Step 2: Set Up WhatsApp Sandbox (for testing)
1. Go to https://console.twilio.com/us1/develop/sms/try-it-out/whatsapp-learn
2. Follow the instructions to join the WhatsApp sandbox
3. You'll get a sandbox number like `whatsapp:+14155238886`

### Step 3: Get Your WhatsApp Number (for production)
1. In Twilio Console, go to Messaging > Try it out > Send a WhatsApp message
2. Use the sandbox number for testing, or purchase a WhatsApp-enabled number for production

## 3. Configure Environment Variables

Set the following environment variables (recommended for production):

```bash
# Windows (PowerShell)
$env:TWILIO_ACCOUNT_SID="your-account-sid"
$env:TWILIO_AUTH_TOKEN="your-auth-token"
$env:TWILIO_WHATSAPP_FROM="whatsapp:+14155238886"
$env:TWILIO_WHATSAPP_TO="whatsapp:+1234567890"

# Linux/Mac
export TWILIO_ACCOUNT_SID="your-account-sid"
export TWILIO_AUTH_TOKEN="your-auth-token"
export TWILIO_WHATSAPP_FROM="whatsapp:+14155238886"
export TWILIO_WHATSAPP_TO="whatsapp:+1234567890"
```

**Or** update `Backend/config.py` directly (not recommended for production):

```python
TWILIO_ACCOUNT_SID = "your-account-sid"
TWILIO_AUTH_TOKEN = "your-auth-token"
TWILIO_WHATSAPP_FROM = "whatsapp:+14155238886"  # Your Twilio WhatsApp number
TWILIO_WHATSAPP_TO = "whatsapp:+1234567890"     # Recipient number (format: whatsapp:+countrycode+number)
```

## 4. Update Your DAS Script

Update the script URL to point to your backend:

```python
import requests
import sys
import json

symbol = sys.argv[1]
price = sys.argv[2]
alert_type = sys.argv[3]
shares = sys.argv[4]

payload = {
    "source": "DAS",
    "symbol": symbol,
    "price": price,
    "shares": shares,
    "alert": alert_type
}

# Update this URL to your backend URL
requests.post(
    "https://api.yourapp.com/webhook/das",  # Change to your backend URL
    json=payload,
    timeout=5
)
```

## 5. Test the Webhook

### Test with curl:

```bash
curl -X POST http://localhost:8000/webhook/das \
  -H "Content-Type: application/json" \
  -d '{
    "source": "DAS",
    "symbol": "AAPL",
    "price": "150.50",
    "shares": "100",
    "alert": "BUY"
  }'
```

### Expected Response:

```json
{
  "status": "success",
  "message": "Signal received and WhatsApp notification sent",
  "data": {
    "symbol": "AAPL",
    "price": "150.50",
    "shares": "100",
    "alert": "BUY"
  }
}
```

## 6. WhatsApp Message Format

The webhook will send WhatsApp messages in this format:

```
🚨 DAS Trading Alert 🚨

Symbol: AAPL
Price: $150.50
Shares: 100
Alert Type: BUY
Source: DAS
Time: 2025-12-19 10:30:45
```

## 7. Troubleshooting

### WhatsApp message not sending:
1. Check that Twilio credentials are set correctly
2. Verify the WhatsApp number format: `whatsapp:+countrycode+number` (e.g., `whatsapp:+14155551234`)
3. For sandbox testing, make sure you've joined the Twilio WhatsApp sandbox
4. Check backend logs for error messages

### Webhook not receiving requests:
1. Verify the endpoint URL is correct: `https://your-backend-url/webhook/das`
2. Check CORS settings if calling from a browser
3. Verify the payload format matches the expected schema
4. Check backend logs for incoming requests

### Common Errors:

**"Twilio not configured"**
- Set `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` environment variables

**"Invalid phone number"**
- Ensure phone numbers are in format: `whatsapp:+countrycode+number`
- Country code should not include the leading `+` in the number itself

**"WhatsApp not enabled"**
- For production, you need a WhatsApp-enabled Twilio number
- For testing, use the WhatsApp sandbox

## 8. Security Considerations

- The `/webhook/das` endpoint is **public** (no authentication required)
- Consider adding:
  - API key authentication
  - IP whitelisting
  - Rate limiting
  - Request signature validation

Example with API key:

```python
@app.post("/webhook/das")
async def receive_das_signal(
    signal: DasSignalRequest,
    api_key: str = Header(None, alias="X-API-Key")
):
    if api_key != os.getenv("WEBHOOK_API_KEY"):
        raise HTTPException(status_code=401, detail="Invalid API key")
    # ... rest of the code
```

## 9. Production Deployment

1. Use environment variables for all sensitive credentials
2. Enable HTTPS for your backend
3. Set up proper logging and monitoring
4. Consider adding rate limiting to prevent abuse
5. Use Twilio's production WhatsApp API (requires approval)

## Support

For Twilio support:
- Documentation: https://www.twilio.com/docs/whatsapp
- Support: https://support.twilio.com/

