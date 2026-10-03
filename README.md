# AutoPost Studio

AutoPost Studio is a Windows-friendly desktop application for compositing photos with transparent PNG frames, organizing post drafts, and publishing or scheduling photo posts to Facebook Pages. Its interface is built with PySide6 and image operations use Pillow.

> **Posting scope:** Meta's Graph API supports publishing to Facebook Pages the signed-in user is permitted to manage. It does not support posting to a personal Facebook profile from this application. Google Drive import and automatic update checks are not implemented yet.

## Quick start

Use Python 3.11 or newer. From the project folder:

```powershell
python -m pip install -r requirements.txt
python main.py
```

In the app, select a transparent frame PNG, select a source image, write a caption if desired, and choose **Export image**. To process a batch, choose **File → Load multiple images** and select one or more files, or use **File → Load image folder** to select every supported image in a directory. Batch mode disables the single-image field, shows up to eight framed previews with a remaining-image count, and changes the export action to export the selection. Supported formats include JPG/JPEG, PNG, WebP, BMP, TIFF, and TIF.

## Features

- **File:** load a frame or photo, select multiple images or a folder of photos for batch processing, open the post scheduler, edit caption assistant settings, reset the preview. Google Drive is explicitly reserved for a future release.
- **View:** light/dark appearance and a single-prompt terminal-style app console. It accepts the same application automation commands as the standalone CLI; it is not a general-purpose operating-system shell.
- **Options:** Facebook Page credentials, AI caption provider settings, and the scheduler folder.
- **About:** application version and developer/about information.
- **Post metadata:** each image exported by the desktop app receives a `.autopost` JSON sidecar containing its image path, caption, schedule, destination, Page ID, status, and remote post ID. By default the sidecar is in the output folder beside its image; if a separate scheduler folder is configured, new sidecars are saved there and refer to the image by absolute path. The scheduler also lists supported image files without a sidecar as editable draft posts. Sidecars are ordinary JSON and can be backed up or inspected with a text editor.
- **Local scheduler:** select a row's date/time using the calendar popup and queue posts for local publishing. The separate background worker starts when AutoPost Studio opens and keeps running after the main window closes. On Windows it can be stopped from Task Manager; it is not automatically started after a Windows restart.
- **Scheduler deletion:** select one or multiple rows and choose **Delete selected**. After confirmation, the matching sidecar files and image files are deleted. Images shared by another remaining scheduler record are preserved.
- **Credential health:** the background worker checks the saved Facebook Page token and configured hosted caption-provider keys at startup and periodically. Providers may not reveal token expiration dates; invalid/revoked credentials are detected from authentication responses and shown as Windows notifications. Ensure Windows notifications are enabled.
- **Facebook publishing:** select one or more rows in the scheduler (Ctrl/Shift-click), then choose **Individual posts** to publish each image as a separate post or **One album post** to combine 2–10 images into one post. Individual posts use each row's caption. An album uses the first selected row's caption and requires the same Page ID; locally queued albums require the same schedule time on every selected row.
- **Facebook image fit:** the live preview shows the composed image at its actual aspect ratio. Facebook uploads are optimized as JPEG without adding a canvas, side fill, or crop, so the uploaded file contains the image itself only. Facebook's own surrounding Page/feed layout may still show its display background depending on screen width; the app does not bake any extra white, brown, or blurred bars into the photo.
- **Automation CLI:** frame image folders recursively, inspect the queue as JSON, locally queue posts, and publish/schedule posts through Facebook Page APIs.

## Data and output folders

In a source checkout, the app uses folders inside the project directory. For a PyInstaller executable, it uses the directory containing the `.exe`. The app creates these folders when needed:

```text
AutoPost Studio/
├── data/
│   ├── preferences.json
│   └── recent_activity.json
├── logs/
│   └── frame_studio.log
└── outputs/
    ├── image_framed.png
    └── image_framed_<id>.autopost
```

When a separate scheduler folder is chosen, sidecars are saved in that folder instead of alongside the output images.

Rendered images use the selected output folder; the default is `outputs`. The scheduler reads `.autopost` records from its configured folder. Choose **Options → Scheduler folder…** to point it at another directory. Credentials are stored with the operating system's credential vault through `keyring`; access tokens are not written to `preferences.json`.

## Application configuration and icon

Application identity and runtime locations are centralized in [`src/config.py`](./src/config.py). To change the product name/version or point to another icon, update `APP_NAME`, `APP_VERSION`, or `ICON_RESOURCE` there. The application icon file `app_icon.ico` is in the project root beside `main.py`; the optional default frame asset is `frame_sample.png`. Add other bundled read-only resources and resolve them with `resource_path(...)`. User data, logs, and output directories are kept separate from bundled resources so packaged apps can read their assets without writing into the bundle.

## Facebook Page API setup

### Why `publish_actions` fails

The error `(#200) ... publish_actions ... has been deprecated` is a **permission error**, not proof that the token has expired. Meta retired `publish_actions`; generating another token with that old permission will not fix it. The current AutoPost Studio Page-photo and Page-feed requests do not ask for `publish_actions`. This error indicates the token/app configuration is stale or does not carry suitable current Page permissions. The app now explains this error and points to the System User setup below.

Use a Meta app and a Facebook Page on which the business can create content. For current Page publishing endpoints Meta requires `pages_manage_posts` and may require additional Page permissions (for example `pages_show_list`, `pages_read_engagement` / `pages_manage_read_engagement`, or `pages_manage_metadata`, depending on the endpoint and current API guidance). Request the exact permissions listed for your endpoint in Meta's [Pages API guide](https://developers.facebook.com/documentation/pages-api/getting-started) and [Page photo reference](https://developers.facebook.com/docs/graph-api/reference/page/photos/). Do **not** request `publish_actions`.

### Recommended for a business-owned Page: System User token

For an app posting to Pages owned/managed by your business portfolio, use a System User token instead of a token copied from Graph API Explorer's temporary login.

1. In [Meta for Developers](https://developers.facebook.com/), create/select the app used for this integration. Confirm the app is configured for the relevant Pages API and that the required permissions/access level are available for your business and intended use.
2. Open [Meta Business Settings](https://business.facebook.com/settings/), go to **Users → System Users**, and create a System User. Use the least-privileged role that can manage the required Page/app assets.
3. Assign the target **Page** to the System User with the permission/task that allows creating content. Assign the **Meta app** to that System User as required by the dashboard.
4. Select the System User and choose **Generate token**. Select the correct app, grant only the current Page publishing permissions required by the endpoints, and choose the longest supported expiration option (Meta may offer a non-expiring option for eligible System User setups). Never request `publish_actions`.
5. In AutoPost Studio open **Options → APIs…**, enter the target Page ID and System User token, then choose **Test Page access**. This makes a read-only Graph API request to confirm the token can access that Page; it does not publish or fully validate write permissions. Save when the Page name is shown.
6. Select the Page ID for the posts in the scheduler and try a normal publish. If Meta still rejects publishing, check the Page asset assignment, app assignment, token's granted permissions, app access/review status, and Page content-creation task in Business Settings.

An access token stored by AutoPost Studio is persisted in the operating-system credential vault (Windows Credential Manager); it is not stored in the preferences file. **Persistence of the stored string is different from extending its validity:** Meta can still expire or revoke it if the token's selected expiration is reached, a permission is removed, the app or Page assignment changes, the System User is disabled, or Meta invalidates it for security/policy reasons. Check Meta's token debugger / Business Settings token details when posting later fails, then generate a replacement and save it in the app. Do not share tokens or put them in screenshots, source code, logs, sidecars, or command history.

Do not put a Meta **App Secret** in the desktop application or executable. For a distributed app with unrelated Page owners, use a proper Facebook Login OAuth flow and a secure server to perform app-secret token exchanges; a desktop binary cannot safely hold that secret. Meta describes System User tokens and token lifetimes in its [access token documentation](https://developers.facebook.com/documentation/facebook-login/guides/access-tokens/). Even tokens described as long-lived or non-expiring remain revocable.

In **Post Scheduler**, select one or more rows (Ctrl/Shift-click), choose **Individual posts** or **One album post**, and set the Page ID and schedule time (`YYYY-MM-DD HH:MM`). Choose **Queue selection** to save a local schedule; it is not submitted to Meta ahead of time. The background worker publishes it when the selected local time arrives. Individual posts use each row's caption. An album uses the first selected row's caption and requires one shared schedule time. **Publish selection now** publishes immediately after confirmation. Merely saving a local draft does not queue or publish it.

The command-line publishing utility's optional `--schedule` argument uses Meta's scheduled-publishing API; its accepted schedule window and other limits are enforced by Meta and may change. The desktop scheduler instead queues work locally and requires its background worker to be running at the selected time. Never share an access token. Revoke compromised tokens from Meta's business/developer settings.

Album posts use Meta's supported multi-photo Page post flow: upload each image as unpublished, then submit one `/page-id/feed` post with the uploaded photo IDs. Locally queued albums use this flow when their scheduled time arrives. The command-line `--schedule` option uses Meta's scheduled feed request. If the feed request fails after photos were uploaded, the app reports this; unused unpublished uploads are temporary and Meta removes them after about 24 hours. See Meta's [Page photo reference](https://developers.facebook.com/docs/graph-api/reference/page/photos/) for current API details.

CLI publishing can use `FB_PAGE_ID` and `FB_PAGE_TOKEN` environment variables, or the saved credential-vault token:

```powershell
$env:FB_PAGE_ID = "YOUR_PAGE_ID"
$env:FB_PAGE_TOKEN = "YOUR_PAGE_ACCESS_TOKEN"
python fb_post.py .\outputs\image_framed.png "A new post from AutoPost Studio"
python fb_post.py .\outputs\image_framed.png "Launching soon" --schedule "2026-10-10 18:30"
```

Environment variables exist only in the current PowerShell process and are a convenient automation alternative. Avoid putting the token directly into a script or command history.
The original `test.py` publishing entry point remains as a compatibility alias; new scripts should use `fb_post.py`.

## AI caption assistant

AutoPost Studio supports four interchangeable **text-only** providers. Each provider has its own saved model, endpoint, and credential-vault API key. Switching providers in **Options → Captions…** restores that provider's settings. Caption requests use keywords and the existing caption text as context; **no image is read or uploaded**.

| Provider | Setup | Suggested model / endpoint |
| --- | --- | --- |
| **Groq** | Create a key in [Groq Console](https://console.groq.com/keys). The endpoint is handled by the official SDK. | `llama-3.3-70b-versatile` |
| **OpenRouter** | Create a key in [OpenRouter](https://openrouter.ai/settings/keys). | Endpoint `https://openrouter.ai/api/v1`; model `openrouter/free` or another model ID available to your account. |
| **Gemini** | Create a Gemini API key in [Google AI Studio](https://aistudio.google.com/apikey). The endpoint is handled by Google's SDK. | `gemini-2.5-flash-lite` |
| **Ollama** | Install and run Ollama locally, then pull a text model (for example `ollama pull gemma3:1b`). No API key required. | Endpoint `http://localhost:11434`; model `gemma3:1b`. |

In **Options → Captions…**, select a provider, enter its model and required API key, then set optional comma-separated keywords and a caption template. Ollama does not need a key. API keys are stored separately per provider in the operating-system credential vault, not in `preferences.json`. The supported template placeholders are `{caption}`, `{keywords}`, and `{hashtags}`; the app applies the template locally after receiving the model's text.

Choose **Generate caption**; an image is not required. Review and edit the generated text before publishing. Hosted-provider free tiers, model access, rate limits, and quotas are controlled by each provider and may change. Never share or commit API keys.

The app reports connection, response, and configuration errors and does not silently replace a failed result with generated-looking text. Install the `groq` package with `python -m pip install -r requirements.txt` when upgrading an existing source checkout.

## Automation CLI

The standalone automation entry point supports scripting and can also be used in the in-app terminal. It does not execute arbitrary PowerShell, Command Prompt, or shell commands.

```powershell
# Show workspace and scheduler counts (use --json for scripts)
python main.py cli status --json

# Frame one photo
python main.py cli frame .\frame.png .\portrait.jpg --output .\outputs\portrait.png

# Frame a folder, including nested folders while preserving their relative paths
python main.py cli frame .\frame.png .\incoming --output .\outputs --recursive --fit contain

# Queue an image for local publishing by the background worker
python main.py cli queue add .\outputs\portrait.png --caption "A new post" --schedule "2026-10-05 18:30" --page-id YOUR_PAGE_ID

# List queue entries, filter by status, and emit JSON for automation
python main.py cli queue list --status queued --json

# Delete requires explicit confirmation and removes the sidecar and unshared image
python main.py cli queue delete POST_ID --yes

# Publish immediately, or submit directly to Meta's schedule with --schedule
$env:FB_PAGE_ID = "YOUR_PAGE_ID"
$env:FB_PAGE_TOKEN = "YOUR_PAGE_ACCESS_TOKEN"
python main.py cli publish .\outputs\portrait.png "A new post" --dry-run
python main.py cli publish .\outputs\portrait.png "A new post"
python main.py cli publish .\outputs\portrait.png "A new post" --schedule "2026-10-05 18:30"
```

`queue add` requires a future local time and uses the saved Page ID if `--page-id` is omitted. It writes a `.autopost` sidecar to the configured scheduler folder. The GUI background worker must be running when the post becomes due. `queue delete` deliberately requires `--yes` because it removes files; a shared image is preserved if another post references it. The `publish` command sends the request to Facebook immediately unless `--schedule` is supplied, which delegates scheduled publishing to Meta.

Run `python main.py cli --help`, `python main.py cli queue --help`, or `python main.py cli frame --help` to see options. The in-app terminal supports command history with the Up/Down keys. Paths containing spaces should be quoted.

## Image CLI details

The image CLI is also useful for repeatable local automation:

```powershell
# One image; output defaults to outputs\portrait_framed.png
python main.py cli frame .\frame.png .\portrait.jpg

# A directory; outputs files to outputs by default
python main.py cli frame .\frame.png .\incoming-photos

# Keep the whole image in view instead of cropping to fill
python main.py cli frame .\frame.png .\portrait.jpg --fit contain

# Tune JPEG output quality and crop anchor (0 to 1)
python main.py cli frame .\frame.png .\portrait.jpg --quality 92 --anchor 0.5 0.35

# See all arguments
python main.py cli frame --help
```

CLI options include `--output`, `--fit cover|contain`, `--anchor X Y`, `--quality 1..100`, `--recursive`, and `--verbose`. By default, CLI-framed images are saved to the app's `outputs` folder (beside the executable when packaged); use `--output` to select another destination. The frame should be a transparent PNG. Folder mode processes supported images directly within the folder by default; `--recursive` includes nested folders and preserves their relative output paths. It reports skipped images and returns a non-zero status if processing is incomplete.

## Build a Windows executable

From a clean Python environment in the project directory:

```powershell
python -m pip install -r requirements.txt
python -m pip install pyinstaller
pyinstaller --noconfirm --windowed --name "AutoPost Studio" --icon "app_icon.ico" --add-data "app_icon.ico;." --add-data "frame_sample.png;." main.py
```

For a repeatable build recipe, a matching spec file is also included at `AutoPost_Studio.spec`. It bundles the application icon and default sample frame without needing to retype the `--add-data` arguments manually. The executable is written below `dist\AutoPost Studio\`. The root-level `app_icon.ico` sets the Windows executable icon and is also loaded for the Qt application/window; replace it to customize the icon. Distribute the complete PyInstaller output (or use PyInstaller's `--onefile` if desired) and ensure the account running it can write beside the executable; otherwise choose an output folder with write permission. The app creates `data`, `logs`, and `outputs` alongside the executable at first launch. Remove `--add-data "frame_sample.png;."` if you do not want the default frame.

## Development and validation

- `src/imaging.py`: image loading and compositing.
- `src/scheduler.py`: `.autopost` JSON records.
- `src/scheduler_service.py`: detached local scheduling worker.
- `src/captions.py`: text-only Groq, OpenRouter, Gemini, and Ollama caption requests.
- `src/facebook.py`: Facebook Page Graph API requests.
- `src/storage.py`: preferences, activity, application paths, and logs.
- `src/config.py`: product metadata, runtime directories, and bundled resource paths.
- `src/app.py`: PySide6 desktop interface.
- `src/cli.py`: image automation CLI.

Install dependencies using `python -m pip install -r requirements.txt`. Run tests using `python -m unittest discover -s tests`.
