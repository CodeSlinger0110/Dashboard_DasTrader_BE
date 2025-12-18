# WebSocket Setup Guide for Production

## Problem: 404 Not Found for `/ws` endpoint

When hosting behind a reverse proxy (nginx/Apache), WebSocket connections require special configuration to work properly. The reverse proxy needs to:

1. **Upgrade HTTP connections to WebSocket** - Handle the `Upgrade` header
2. **Forward WebSocket protocol** - Maintain the WebSocket connection
3. **Set proper timeouts** - WebSocket connections are long-lived

## Solution: Configure Your Reverse Proxy

### For Nginx

1. **Edit your nginx configuration** (usually in `/etc/nginx/sites-available/your-site` or `/etc/nginx/nginx.conf`):

```nginx
server {
    listen 443 ssl http2;
    server_name api.diamondtradingpro.com;

    # SSL configuration
    ssl_certificate /path/to/your/certificate.crt;
    ssl_certificate_key /path/to/your/private.key;

    # WebSocket endpoint - CRITICAL CONFIGURATION
    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        
        # These headers are REQUIRED for WebSocket
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # Standard proxy headers
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket needs long timeouts (24 hours)
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
        
        # Disable buffering for WebSocket
        proxy_buffering off;
    }

    # Regular API endpoints
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

2. **Test and reload nginx**:
```bash
sudo nginx -t
sudo systemctl reload nginx
```

### For Apache

1. **Enable required modules**:
```bash
sudo a2enmod proxy
sudo a2enmod proxy_http
sudo a2enmod proxy_wstunnel
sudo a2enmod rewrite
sudo a2enmod headers
```

2. **Edit your Apache virtual host** (usually in `/etc/apache2/sites-available/your-site.conf`):

```apache
<VirtualHost *:443>
    ServerName api.diamondtradingpro.com
    
    # SSL configuration
    SSLEngine on
    SSLCertificateFile /path/to/your/certificate.crt
    SSLCertificateKeyFile /path/to/your/private.key

    # WebSocket endpoint
    ProxyPass /ws ws://127.0.0.1:8000/ws
    ProxyPassReverse /ws ws://127.0.0.1:8000/ws

    # Regular API endpoints
    ProxyPass / http://127.0.0.1:8000/
    ProxyPassReverse / http://127.0.0.1:8000/

    # WebSocket upgrade handling
    RewriteEngine on
    RewriteCond %{HTTP:Upgrade} websocket [NC]
    RewriteCond %{HTTP:Connection} upgrade [NC]
    RewriteRule ^/ws(.*)$ ws://127.0.0.1:8000/ws$1 [P,L]
</VirtualHost>
```

3. **Restart Apache**:
```bash
sudo systemctl restart apache2
```

## Testing

1. **Test the WebSocket endpoint directly** (bypassing proxy):
```bash
curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" \
     -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: test" \
     http://127.0.0.1:8000/ws
```

2. **Test through the proxy**:
```bash
curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" \
     -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: test" \
     https://api.diamondtradingpro.com/ws
```

3. **Check backend logs** for WebSocket connection messages:
```bash
# You should see: "WebSocket connection accepted from ..."
```

## Common Issues

### Issue: Still getting 404
- **Check**: Is nginx/Apache actually forwarding to port 8000?
- **Check**: Is the FastAPI server running on port 8000?
- **Check**: Are there any firewall rules blocking the connection?

### Issue: WebSocket connects but immediately disconnects
- **Check**: Timeout settings in reverse proxy
- **Check**: Backend logs for errors
- **Check**: CORS settings (though WebSocket doesn't use CORS)

### Issue: Connection works but no data
- **Check**: Backend logs for WebSocket messages
- **Check**: Frontend console for WebSocket errors
- **Check**: Network tab in browser DevTools

## Verification Checklist

- [ ] Reverse proxy configured with WebSocket upgrade headers
- [ ] FastAPI server running on port 8000
- [ ] Firewall allows connections to port 8000
- [ ] SSL certificates valid and configured
- [ ] Backend logs show WebSocket connections
- [ ] Frontend can connect to `wss://api.diamondtradingpro.com/ws`

## Quick Reference

- **Backend WebSocket endpoint**: `/ws`
- **Frontend WebSocket URL**: `wss://api.diamondtradingpro.com/ws` (HTTPS → WSS)
- **Backend port**: `8000` (internal)
- **Required headers**: `Upgrade: websocket`, `Connection: upgrade`

