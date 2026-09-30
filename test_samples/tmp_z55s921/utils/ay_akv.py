import os
from azure.identity import ClientSecretCredential, DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

class AyAzureKeyVault:
    def __init__(self):
        tenant_id = os.getenv("AZURE_TENANT_ID")
        client_id = os.getenv("AZURE_CLIENT_ID")
        client_secret = os.getenv("AZURE_CLIENT_SECRET")
        if tenant_id and client_id and client_secret:
            self.credential = ClientSecretCredential(tenant_id, client_id, client_secret)
        else:
            # Fall back to az login / managed identity
            self.credential = DefaultAzureCredential()

    def get_secret(self, vault_uri: str, secret_name: str):
        client = SecretClient(vault_url=vault_uri, credential=self.credential)
        secret = client.get_secret(secret_name)
        return secret.value

    def set_secret(self, vault_uri: str, secret_name: str, secret_value: str):
        client = SecretClient(vault_url=vault_uri, credential=self.credential)
        client.set_secret(secret_name, secret_value)
        return True
