from app.db.mongo import get_db
from app.modules.auth.refresh_repository import RefreshTokensRepository
from app.modules.auth.repository import UsersRepository
from app.modules.auth.service import AuthService
from app.modules.passkeys.repository import PasskeyChallengesRepository, PasskeysRepository
from app.modules.passkeys.service import PasskeyService
from app.modules.verification.repository import VerificationCodesRepository


def get_passkey_service() -> PasskeyService:
    db = get_db()

    users_repo = UsersRepository(db)
    codes_repo = VerificationCodesRepository(db)
    refresh_repo = RefreshTokensRepository(db)

    auth_service = AuthService(
        users=users_repo,
        codes=codes_repo,
        refresh_tokens=refresh_repo,
    )

    return PasskeyService(
        passkeys_repo=PasskeysRepository(db),
        challenges_repo=PasskeyChallengesRepository(db),
        users_repo=users_repo,
        auth_service=auth_service,
    )