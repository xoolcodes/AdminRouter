# AdminRouter

## Setup

Run in a Windows or Linux terminal:

```bash
python setup_adminrouter.py
```

The setup flow will:

1. Ask whether to install optional packages (`flask`, `bcrypt`, `zeroconf`).
2. Prompt for username, password, and port.
3. Hash the password with SHA-256.
4. Generate `adminrouter_config.sql` with the stored credentials/port.
5. Generate `adminrouter_service.py` and configure startup background execution.
