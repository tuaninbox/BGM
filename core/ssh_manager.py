import asyncio, re
from scrapli.driver.core import (
    AsyncIOSXEDriver,
    AsyncNXOSDriver,
    AsyncIOSXRDriver,
    AsyncJunosDriver,
)

# Map vendor -> Scrapli driver
SCRAPLI_DRIVERS = {
    "ios": AsyncIOSXEDriver,
    "nxos": AsyncNXOSDriver,
    # "iosxr": AsyncIOSXRDriver,
    # "junos": AsyncJunosDriver,
}

PROMPT_REGEX = re.compile(r"([A-Za-z0-9._-]+(?:\([^)]+\))?#)\s*$")

def extract_prompt(output: str) -> str:
    lines = output.splitlines()
    for line in reversed(lines):
        m = PROMPT_REGEX.search(line.strip())
        if m:
            return m.group(1) + " "
    return "> "  # fallback prompt


class SSHManager:
    def __init__(self, app):
        self.app = app
        self.sessions: dict[str, any] = {}

    def get_session(self, device_id):
        return self.sessions.get(device_id)

    async def _create_connection(self, device: dict):
        driver_cls = SCRAPLI_DRIVERS.get(device["os"].lower())
        if not driver_cls:
            raise ValueError(f"Unsupported platform: {device['os']}")

        username = device.get("username")
        password = device.get("password")

        if not username or not password:
            creds = self.app.state.credential_loader(self.app.state.config)
            username = creds["tacacs"]["username"] or creds["credentials"]["username"]
            password = creds["tacacs"]["password"] or creds["credentials"]["password"]

        conn = driver_cls(
            host=device["ip"],
            auth_username=username,
            auth_password=password,
            auth_strict_key=False,
            timeout_ops=30,
            transport="asyncssh",
        )

        return conn

    async def run_single_command(self, device: dict, command: str) -> str:
        conn = await self._create_connection(device)
        await conn.open()
        try:
            result = await conn.send_command(command)
            return result.result
        finally:
            await conn.close()

    # async def start_session(self, device: dict):
    #     conn = await self._create_connection(device)
    #     self.sessions[device["id"]] = conn
    #     return conn

    async def start_session(self, device: dict):
            conn = await self._create_connection(device)
            await conn.open()
    
            # Force device to send prompt
            raw = await conn.channel.send_input("")
            stdout = raw[0].decode(errors="ignore") if isinstance(raw, tuple) else raw
            prompt = extract_prompt(stdout)
    
            # ⭐ FIX: store session using device["id"]
            self.sessions[device["id"]] = conn
    
            return conn, prompt
    
    async def send_interactive(self, device_id: str, command: str):
        conn = self.sessions.get(device_id)
        if not conn:
            raise RuntimeError("Session not found")

        raw = await conn.channel.send_input(command)

        if isinstance(raw, tuple):
            stdout, stderr = raw
            stdout = stdout.decode(errors="ignore")
            stderr = stderr.decode(errors="ignore")
            output = stdout if stdout.strip() else stderr
        else:
            output = raw.decode(errors="ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)

        prompt = extract_prompt(output)

        if not output.strip():
            output = prompt

        return output, prompt

    async def close_session(self, device_id: str):
        conn = self.sessions.pop(device_id, None)
        if conn:
            await conn.close()

# ---------------------------------------------------------
# Example usage
# ---------------------------------------------------------
async def main():
    manager = SSHManager()

    device = {
        "id": "sw01",
        "name": "Core Switch",
        "ip": "192.168.2.86",
        "username": "admin",
        "password": "cisco",
        "os": "ios",   # iosxe, nxos, iosxr, junos
    }

    print("\n--- Single Command ---")
    output = await manager.run_single_command(device, "show version")
    print(output)

    print("\n--- Interactive Session ---")

    # Start session
    session_started = await manager.start_session(device)
    if session_started:
        print(f"Connected to {device['name']} ({device['ip']})")
    else:
        print("Failed to start session")
        return

    device_id = device["id"]

    # Interactive loop
    try:
        while True:
            cmd = input(f"{device_id }# ").strip()

            if cmd.lower() in ("exit", "quit"):
                print("Closing session...")
                break

            if not cmd:
                continue

            try:
                output = await manager.send_interactive(device_id, cmd)
                print(output)
            except Exception as exc:
                print(f"Error: {exc}")
                break

    finally:
        await manager.close_session(device_id)
        print("Session closed.")


if __name__ == "__main__":
    asyncio.run(main())
