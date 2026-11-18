import os
import pathlib

def _notify_failure(error):
    try:
        from .notify import telegram_message

        run_url = ""
        if os.environ.get("GITHUB_RUN_ID"):
            run_url = (
                f"\nRun: {os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}"
                f"/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
            )
        sent = telegram_message(
            f"⚠️ Autovid-এ exception হয়েছে: {type(error).__name__}: {str(error)[:900]}{run_url}"
        )
        runner_temp = os.environ.get("RUNNER_TEMP")
        if sent and runner_temp:
            pathlib.Path(runner_temp, "autovid-error-notified").touch()
    except Exception as notify_error:
        print(f"Could not send Telegram exception alert: {notify_error}")


try:
    from .cli import main

    main()
except Exception as error:
    _notify_failure(error)
    raise
except SystemExit as error:
    if error.code not in (None, 0):
        _notify_failure(error)
    raise
