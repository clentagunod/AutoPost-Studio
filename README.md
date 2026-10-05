# AutoPost Studio

AutoPost Studio is a Windows-friendly desktop application for compositing photos with transparent PNG frames, organizing post drafts, and publishing or scheduling photo posts to Facebook Pages. Its interface is built with PySide6 and image operations use Pillow.

> **Posting scope:** Meta's Graph API supports publishing to Facebook Pages the signed-in user is permitted to manage. It does not support posting to a personal Facebook profile from this application. Automatic update checks are not implemented yet.

## Quick start

Use Python 3.11 or newer. From the project folder:

```powershell
python -m pip install -r requirements.txt
python main.py
```

In the app, select a transparent frame PNG, select a source image, write a caption if desired, and choose **Export image**. Choose **Inspect full size** or double-click the live preview to inspect a single photo's full-size composition. To process a batch, choose **File → Load multiple images**, **File → Load image folder**, or **File → Import photos from Google Drive**. Batch mode disables the single-image field, shows a scrollable preview for every selected photo, and changes the export action to export the selection. Select a photo and double-click it or choose **Zoom selected** to inspect its full-size composition; use **Ctrl+scroll** or a touchpad pinch gesture to zoom naturally, and scroll normally to pan. Turn off **Use frame overlay** in the creator workspace to export photos without applying a frame. Supported formats include JPG/JPEG, PNG, WebP, BMP, TIFF, and TIF.

## Features

- **File:** load a frame or photo, select multiple images or a folder of photos for batch processing, browse Google Drive folders and import selected photos, open the post scheduler, edit caption assistant settings, reset the preview.
- **View:** light/dark appearance and an embedded Windows PowerShell console with a persistent working directory and AutoPost automation commands.
- **Options:** Google Drive OAuth setup, multiple Facebook Page names, IDs, and access tokens, AI caption provider settings, and the scheduler folder. Choose the active Page from the current-Page selector above the workspace. Drive and Facebook authorizations are stored in the operating system credential vault.
- **About:** application version and developer/about information.
- **Post metadata:** each image exported by the desktop app receives a `.autopost` JSON sidecar containing its image path, caption, schedule, destination, Page ID, status, and remote post ID. By default the sidecar is in the output folder beside its image; if a separate scheduler folder is configured, new sidecars are saved there and refer to the image by absolute path. The scheduler also lists supported image files without a sidecar as editable draft posts. Sidecars are ordinary JSON and can be backed up or inspected with a text editor.
- **Meta scheduling:** **Schedule with Facebook** submits a scheduled-publishing request to Meta immediately. After Meta accepts it, Meta publishes the post even when AutoPost Studio is closed or the computer is off. The app does not run a detached scheduler or retry a submission that Meta did not accept.
- **Managing scheduled posts:** use **Manage schedules in Meta** or Meta Business Suite to view, edit, pause, or cancel accepted schedules. Meta is the source of truth. **Delete selected** in AutoPost Studio only removes local metadata/images; it does not cancel a post stored by Meta.
- **Scheduler deletion:** select one or multiple rows and choose **Delete selected**. After confirmation, the matching sidecar files and image files are deleted. Images shared by another remaining scheduler record are preserved.
- **Facebook publishing:** select one or more rows in the scheduler (Ctrl/Shift-click), then choose **Individual posts** to publish each image as a separate post or **One album post** to combine 2–10 images into one post. Individual posts use each row's caption. An album uses the first selected row's caption and requires the same Page ID. For an album schedule, set the shared date/time once and choose **Schedule with Facebook**.
- **Facebook image fit:** the live preview shows the composed image at its actual aspect ratio. Facebook uploads are optimized as JPEG without adding a canvas, side fill, or crop, so the uploaded file contains the image itself only. Facebook's own surrounding Page/feed layout may still show its display background depending on screen width; the app does not bake any extra white, brown, or blurred bars into the photo.
- **In-app automation console:** frame image folders recursively, inspect saved post metadata, run Windows shell commands, and publish/schedule posts through Facebook Page APIs.

## Google Drive photo import

Drive import uses Google's read-only Drive API scope. Set it up once:

1. In [Google Cloud Console](https://console.cloud.google.com/), create or select a project and enable the **Google Drive API**.
2. Configure the OAuth consent screen and add your Google account as a test user if the app remains in testing.
3. Create an OAuth client ID with application type **Desktop app**, then download its JSON file.
4. In AutoPost Studio, choose **Options → Google Drive…**, select that JSON file, and save.
5. Choose **File → Import photos from Google Drive…**. On first use, the app opens a browser for Google sign-in and read-only consent. Then browse folders, check supported image files, and choose **Import selected**.

The OAuth refresh token is stored in the operating system credential vault. Imported copies are kept under the app's data directory in `data/google_drive_imports` so the existing preview/export workflow can use them offline. **Options → Google Drive → Disconnect Google Drive** removes the saved authorization; it does not delete imported photos. Only image files with supported extensions are shown.

## Data and output folders

In a source checkout, the app uses folders inside the project directory. For a packaged app, it stores writable data under the current user's application data directory (`%LOCALAPPDATA%\AutoPost Studio` on Windows), not beside the executable. The app creates these folders when needed:

```text
%LOCALAPPDATA%\AutoPost Studio\
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

The **About → Developer information** dialog includes an optional **Buy me a coffee** section. Donations are accepted in any amount through GCash only. Replace `gcash_qr_placeholder.svg` with your GCash QR image, keeping the same filename, and rebuild; you do not need to move or separately ship the image. If you use another filename or place it in a subfolder, set the relative path in `GCASH_QR_RESOURCE` in `src/config.py` first. `AutoPost_Studio.spec` bundles that configured image at the matching internal path and reports a clear build error if it is missing. The checked-in SVG is explicitly marked as a non-scannable placeholder.

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
5. In AutoPost Studio open **Options → Facebook Pages…**, enter the target Page ID and System User token, then choose **Test Page access**. This makes a read-only Graph API request to confirm the token can access that Page; it does not publish or fully validate write permissions. Save when the Page name is shown.
6. Select the Page ID for the posts in the scheduler and try a normal publish. If Meta still rejects publishing, check the Page asset assignment, app assignment, token's granted permissions, app access/review status, and Page content-creation task in Business Settings.

For people outside the app's roles to use a developer app, switch it to Live and request App Review / Advanced Access for the permissions Meta requires. Meta's review/access requirements depend on the app and business configuration; the app dashboard is authoritative. A token that passes the read-only Page access test may still lack permission to create or schedule posts.

An access token stored by AutoPost Studio is persisted in the operating-system credential vault (Windows Credential Manager); it is not stored in the preferences file. **Persistence of the stored string is different from extending its validity:** Meta can still expire or revoke it if the token's selected expiration is reached, a permission is removed, the app or Page assignment changes, the System User is disabled, or Meta invalidates it for security/policy reasons. Check Meta's token debugger / Business Settings token details when posting later fails, then generate a replacement and save it in the app. Do not share tokens or put them in screenshots, source code, logs, sidecars, or command history.

Do not put a Meta **App Secret** in the desktop application or executable. For a distributed app with unrelated Page owners, use a proper Facebook Login OAuth flow and a secure server to perform app-secret token exchanges; a desktop binary cannot safely hold that secret. Meta describes System User tokens and token lifetimes in its [access token documentation](https://developers.facebook.com/documentation/facebook-login/guides/access-tokens/). Even tokens described as long-lived or non-expiring remain revocable.

### Schedule from AutoPost Studio

AutoPost Studio sends each schedule request to Meta immediately; it does not retain a local job that needs the app or computer to be running later.

1. Finish the Meta Page/token setup above and confirm **Test Page access** succeeds in **Options → Facebook Pages…**.
2. Open **File → Post Scheduler…**. Make sure each selected row has the correct Facebook Page ID, caption, and image.
3. Select one or more rows using Ctrl-click or Shift-click. Choose **Individual posts** if each image should become its own post, or **One album post** to combine 2–10 images as one Page post.
4. For individual posts, set the desired date/time in each selected row. For an album, set the one **Shared album date/time** control; its selected images are submitted together using the first selected row's caption.
5. Choose **Schedule with Facebook** and confirm. The app uploads the image(s) and sends the scheduled-publishing request to Meta. A successful result appears with status `scheduled` and Meta's post ID; that status records acceptance and is not a live sync of Meta's later publishing state.
6. Open [Meta Business Suite](https://business.facebook.com/) and use its Planner or scheduled content area to confirm the post appears. Meta's navigation labels can vary by account and Page.

The desktop GUI enforces a schedule window of 10 minutes to 30 days ahead. Times are entered in the computer's local time zone; verify the Page's time-zone display in Meta Business Suite. Only schedules Meta has accepted are managed by Meta: the app must have internet while submitting and cannot schedule offline. If submission fails, the app reports the error and leaves the item available to correct and submit again. Avoid retrying when a network timeout leaves acceptance uncertain; first check Meta Business Suite to avoid a duplicate.

To edit, pause, or cancel an accepted post, use Meta Business Suite. **Delete selected** in AutoPost Studio removes local sidecar/image files only and does not cancel or change Meta's copy. Previously saved `queued` entries from the retired local-worker version were never submitted to Meta; select them and use **Schedule with Facebook** to submit them now. If an older app version left its detached worker running, close that old process once from Task Manager; this version does not launch it.

Album scheduling uses Meta's multi-photo Page post flow: upload each image as unpublished, then create one scheduled `/page-id/feed` post attaching those photo IDs. If the feed request fails after uploads, AutoPost Studio reports the error; unused unpublished uploads are temporary and Meta may remove them. See Meta's [Page photo reference](https://developers.facebook.com/docs/graph-api/reference/page/photos/) and [Page feed reference](https://developers.facebook.com/docs/graph-api/reference/page/feed/) for current API requirements and limits.

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

## In-app automation console

Open **View → CLI Console…** to run commands inside AutoPost Studio, including in the packaged executable. The console starts in the directory from which the app was launched. Use `cd` to move into a project or image folder; this working directory persists between commands and relative paths in AutoPost commands resolve from it. Paths containing spaces should be quoted.

```powershell
cd "C:\Users\you\Pictures\Campaign"
pwd
Get-ChildItem
frame .\frame.png .\incoming --output .\exports --recursive --fit contain
status --json
queue list --status scheduled
cls
help
exit
```

The console runs Windows PowerShell commands in the current directory. Shell commands run asynchronously; use **Stop command** or **Ctrl+C** to interrupt a running process. `cd`, `pwd`, `cls`, `help`, and `exit` are handled by the embedded console. AutoPost commands include `frame`, `publish`, `status`, and `queue`; run a command with `--help` for its options. For example, `frame --help` lists image-processing options such as `--output`, `--fit`, `--anchor`, `--quality`, and `--recursive`. By default, framed images go into the app's `outputs` folder; specify `--output .\exports` to save relative to the current directory.

PowerShell commands run with the current Windows user's permissions. Avoid pasting commands you do not trust. The standalone `main.py cli` entry point has been removed; use this in-app console for interactive shell and AutoPost automation.

## Build a Windows executable

From a clean Python environment in the project directory, build with the project spec:

```powershell
python -m pip install -r requirements.txt
python -m pip install pyinstaller
pyinstaller --noconfirm AutoPost_Studio.spec
```

The spec bundles the application icon, default sample frame, and the configured QR image. PyInstaller places bundled resources in its internal application resource directory; the app resolves the QR through PyInstaller's runtime resource root, so the QR is not loaded from the current working directory and cannot conflict with a separately copied file. Distribute the complete `dist\AutoPost Studio\` folder produced by this spec; the QR is already inside the package and does not need to sit beside the executable. Logs, preferences, and default outputs are stored per-user under `%LOCALAPPDATA%\AutoPost Studio`.

## Development and validation

- `src/imaging.py`: image loading and compositing.
- `src/scheduler.py`: `.autopost` JSON records.
- `src/captions.py`: text-only Groq, OpenRouter, Gemini, and Ollama caption requests.
- `src/facebook.py`: Facebook Page Graph API requests.
- `src/storage.py`: preferences, activity, application paths, and logs.
- `src/config.py`: product metadata, runtime directories, and bundled resource paths.
- `src/app.py`: PySide6 desktop interface.
- `src/cli.py`: image automation CLI.

Install dependencies using `python -m pip install -r requirements.txt`. Run tests using `python -m unittest discover -s tests`.
