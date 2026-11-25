# FTBLPOV colourway packs

Every two hours the workflow checks @cleats for photos of association football boots. A vision model inspects the views and writes a named pack brief with a palette, material finishes and a colour map for each eligible photo. GPT Image edits each source photo. Later views also receive the first rendered image for colour continuity. The workflow publishes a single image or an ordered carousel to @ftblpov with a pack-specific Instagram caption and an unofficial concept label. It skips videos, unrelated sports and text-only images.

## Configure GitHub Actions

Use a professional Instagram account linked to a Facebook Page and a token with instagram_basic, instagram_content_publish and pages_read_engagement. Add these Actions secrets under repository Settings → Secrets and variables → Actions.

| Secret | Purpose |
| --- | --- |
| IG_USER_ID | Instagram account ID for @ftblpov |
| IG_ACCESS_TOKEN | Meta publishing and Business Discovery access |
| OPENAI_API_KEY | Funded OpenAI project with GPT Image and GPT-4.1 mini access |
| AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY | Access to PutObject and GetObject on the bucket's ftblpov/ prefix |

Set repository variables PACK_BUCKET and AWS_REGION to a private S3 bucket and its region. Configure the bucket to expire objects under ftblpov/ after one day. Each upload is freshly encoded from RGB pixels, stripping embedded metadata. Meta downloads the generated JPEGs using two-hour signed URLs. The bucket may retain files longer if no lifecycle policy is configured. Live runs incur image, vision and storage usage.

The runner needs repository contents write access for the ig-repost-state branch. Merge the code into the default branch to activate the existing schedule. On the first live run, existing source posts are recorded as a baseline. To publish one immediately, run **FTBLPOV colourway packs** from Actions and put its @cleats post URL in **links**. Dry run only lists candidate photos and checks the Instagram account. A live run is required to test actual rendering and publication.

Before publishing, the job pushes the Instagram container ID to the journal. A retry resumes that container and checks whether Meta already published it. An expired container is cleared and regenerated on the next run. Existing repost history is honoured. Render failures before a container is saved can incur generation costs again.

For local use install requirements.txt, export the same settings (use ACCESS_TOKEN for IG_ACCESS_TOKEN), then run python3 repost.py --dry-run. Local state is separate from Actions.
