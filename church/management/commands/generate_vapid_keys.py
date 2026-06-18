import base64
from pathlib import Path

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Generate VAPID keys for browser push notifications."

    def add_arguments(self, parser):
        parser.add_argument(
            "--private-key-path",
            default=".vapid_private_key.pem",
            help="Path where the private key PEM should be written.",
        )

    def handle(self, *args, **options):
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
        except ImportError:
            self.stderr.write(self.style.ERROR("cryptography is required. Install requirements.txt first."))
            return

        private_key = ec.generate_private_key(ec.SECP256R1())
        public_key = private_key.public_key()
        public_numbers = public_key.public_numbers()
        public_bytes = (
            b"\x04"
            + public_numbers.x.to_bytes(32, "big")
            + public_numbers.y.to_bytes(32, "big")
        )
        public_key_b64 = base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode("ascii")
        private_key_path = Path(options["private_key_path"]).expanduser().resolve()
        private_key_path.write_bytes(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

        self.stdout.write(self.style.SUCCESS(f"Private key written to {private_key_path}"))
        self.stdout.write("")
        self.stdout.write("Add these to .env:")
        self.stdout.write(f"VAPID_PUBLIC_KEY={public_key_b64}")
        self.stdout.write(f"VAPID_PRIVATE_KEY_PATH={private_key_path}")
