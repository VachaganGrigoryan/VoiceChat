from app.modules.auth.refresh_repository import RefreshTokensRepository
from app.modules.auth.repository import UsersRepository
from app.modules.auth.service import AuthService
from app.modules.passkeys.repository import PasskeyChallengesRepository, PasskeysRepository
from app.modules.passkeys.service import PasskeyService
from app.modules.verification.repository import VerificationCodesRepository


def get_passkey_service() -> PasskeyService:
    users_repo = UsersRepository()
    codes_repo = VerificationCodesRepository()
    refresh_repo = RefreshTokensRepository()

    auth_service = AuthService(
        users=users_repo,
        codes=codes_repo,
        refresh_tokens=refresh_repo,
    )

    return PasskeyService(
        passkeys_repo=PasskeysRepository(),
        challenges_repo=PasskeyChallengesRepository(),
        users_repo=users_repo,
        auth_service=auth_service,
    )