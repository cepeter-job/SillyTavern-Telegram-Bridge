# Optional puntoap GitHub Actions runner

This is a **manual, opt-in Windows runner**, not a replacement for the Linux CI gate.
The main workflow continues to use `ubuntu-latest` for pull requests and pushes.

## Scope and security model

- Repository: `cepeter-job/SillyTavern-Telegram-Bridge` (public).
- Runner name: `puntoap-on-demand`; labels: `self-hosted`, `Windows`, `X64`, `puntoap`, `on-demand`.
- Installed at `C:\PuntoapGitHubRunner`. The runner must stay **offline** when not specifically needed.
- Workflow `.github/workflows/puntoap-on-demand.yml` is **workflow_dispatch only**, checks that its ref is `main`, uses a read-only `GITHUB_TOKEN`, and disables persisted checkout credentials.
- Never schedule unreviewed PRs or arbitrary branches to this runner. Do not add repository secrets, `pull_request`, `pull_request_target`, or `workflow_run` triggers.
- It is a personal Windows machine. Even under a low-privilege service account, GitHub Actions jobs execute repository code, may access network resources, and are **not equivalent to ephemeral virtual machines**. For untrusted code, use GitHub-hosted runners or a disposable VM.

The runner was registered manually using a short-lived GitHub registration token. No PAT or registration token should be checked into this repository.

## On-demand startup (local machine)

**Do not run `run.cmd` from an elevated administrator session.** Use a dedicated restricted account. A practical Windows option is an on-demand Scheduled Task running as `NT AUTHORITY\LOCAL SERVICE`. The runner directory is ACL-restricted to Administrators, SYSTEM, and LOCAL SERVICE (Modify), so a normal interactive user cannot tamper with runner credentials.

One-time local Scheduled Task configuration in the Windows Task Scheduler GUI, performed by an administrator:

1. Create a task named `Puntoap-GitHub-Runner-OnDemand`. Set its account to `NT AUTHORITY\LOCAL SERVICE`. Do **not** select "Run with highest privileges".
2. Do not set any automatic or recurring triggers. Allow the task to be run on demand.
3. Add an action with program `C:\Windows\System32\cmd.exe`, arguments `/d /c C:\PuntoapGitHubRunner\run.cmd --once`, and Start in `C:\PuntoapGitHubRunner`.
4. Set a bounded execution time (for example, 90 minutes) so it cannot remain connected indefinitely when no job arrives.
5. Verify the task principal, start the task, and confirm the runner becomes `online` in the repository's **Settings → Actions → Runners** page. When it completes its one queued job, `--once` should exit and the runner should return to `offline`.

The `--once` option remains supported in GitHub runner 2.338.0 but is marked for future deprecation. A proper ephemeral-VM design is preferable for long-term autoscaling.

### Start and inspect the runner

Run on the Windows host in a PowerShell window permitted to control the task:

```powershell
Start-ScheduledTask -TaskName 'Puntoap-GitHub-Runner-OnDemand'
Get-ScheduledTask -TaskName 'Puntoap-GitHub-Runner-OnDemand'
gh api repos/cepeter-job/SillyTavern-Telegram-Bridge/actions/runners --jq '.runners[] | [.name,.status,.busy] | @tsv'
```

**Start the task before dispatching to puntoap.** If the machine is offline, GitHub queues the job rather than silently falling back to hosted compute. Only submit one puntoap job at a time.

### Manually request a validation run

In the GitHub **Actions** tab, choose **Optional Windows CI (puntoap) → Run workflow**, select branch `main`, choose `puntoap` or `github-hosted`, and choose `compile` or `pytest`.

Equivalent CLI commands:

```powershell
# Only after starting the on-demand runner task:
gh workflow run puntoap-on-demand.yml -R cepeter-job/SillyTavern-Telegram-Bridge --ref main -f runner=puntoap -f suite=compile

# Alternative that works even when puntoap is offline:
gh workflow run puntoap-on-demand.yml -R cepeter-job/SillyTavern-Telegram-Bridge --ref main -f runner=github-hosted -f suite=compile
```

The `pytest` option installs hash-locked dependencies and runs the suite on Windows. This is **experimental**, and may reveal Windows incompatibilities; the Linux CI remains authoritative.

### Stop the runner

When the runner is idle, stop the task if it did not already exit:

```powershell
Stop-ScheduledTask -TaskName 'Puntoap-GitHub-Runner-OnDemand'
```

Never stop it while a job is running. The job may fail and leave incomplete working files.

## Operations

- No Docker or WSL is required for the Windows smoke workflow.
- The runner supports one job per activation. After completion, start the task again for another run.
- Keep the runner up to date and periodically clean its `_work` directory **only while stopped**.
- There is intentionally no unattended wake-on-dispatch, no open inbound port, and no automatic PR execution. For a fully automatic runner lifecycle, use an isolated ephemeral VM, not a personal Windows desktop.
