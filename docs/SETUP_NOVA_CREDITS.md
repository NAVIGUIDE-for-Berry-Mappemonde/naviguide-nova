# How to Get Nova 2 Lite Working — Credits & Setup

Guide to pay for and use Amazon Nova 2 Lite for the hackathon.

---

## 1. Create or Use an AWS Account

1. Go to [aws.amazon.com](https://aws.amazon.com)
2. **Create account** (or sign in if you have one)
3. Add a **payment method** (credit card) — required for Bedrock
4. New accounts get **12 months free tier** + **$200 credits** for 90 days (varies by region/promo)

---

## 2. Enable Nova 2 Lite in Bedrock

**Amazon models (Nova) are enabled by default** — no manual subscription needed. You just need a valid payment method.

1. Open [Amazon Bedrock Console](https://console.aws.amazon.com/bedrock/)
2. Select region **US East (N. Virginia)** — `us-east-1` (top right)
3. In the left menu: **Model access** (under Bedrock configurations)
4. Check that **Amazon Nova** models are listed and available
5. If you see "Request access" or "Enable" for Nova 2 Lite, click it and confirm

**Note:** First invocation may trigger a short setup (up to 15 min). If you get `AccessDeniedException`, wait a few minutes and retry.

---

## 3. Get Credentials for Your Code

### Option A: IAM User (classic)

1. **IAM** → **Users** → **Create user**
2. Attach policy: `AmazonBedrockFullAccess` (or a custom policy with `bedrock:InvokeModel`)
3. **Security credentials** → **Create access key**
4. Set environment variables:
   ```bash
   export AWS_ACCESS_KEY_ID=AKIA...
   export AWS_SECRET_ACCESS_KEY=...
   export AWS_DEFAULT_REGION=us-east-1
   ```

### Option B: Bedrock API Keys (simpler)

1. [Bedrock Console](https://console.aws.amazon.com/bedrock/) → **API keys** (left menu)
2. **Generate long-term API key**
3. Set expiration (e.g. 30 days for the hackathon)
4. Copy the key
5. **Où mettre la clé** — créer `naviguide_workspace/.env` :
   ```bash
   cp naviguide_workspace/.env.example naviguide_workspace/.env
   # Éditer .env et coller ta clé :
   # AWS_BEARER_TOKEN_BEDROCK=ta_clé_ici
   ```
   Ou en variable d'environnement avant de lancer :
   ```bash
   export AWS_BEARER_TOKEN_BEDROCK=your_api_key_here
   ./naviguide_workspace/start_local.sh
   ```

**Reference:** [Accelerate AI development with Amazon Bedrock API keys](https://aws.amazon.com/blogs/machine-learning/accelerate-ai-development-with-amazon-bedrock-api-keys/)

---

## 4. Nova 2 Lite Pricing (very cheap)

| | Price |
|---|-------|
| **Input tokens** | ~$0.30 per 1M tokens |
| **Output tokens** | ~$2.50 per 1M tokens |

**Example:** One briefing = ~2K input + ~500 output tokens ≈ **$0.001** (0.1 cent)

For the hackathon (dozens of tests + demo): **under $1 total**.

---

## 5. Verify It Works

```bash
# With IAM credentials or API key set
python3 -c "
import boto3
client = boto3.client('bedrock-runtime', region_name='us-east-1')
r = client.converse(
    modelId='amazon.nova-2-lite-v1:0',
    messages=[{'role': 'user', 'content': [{'text': 'Say hello in one word'}]}]
)
print(r['output']['message']['content'][0]['text'])
"
```

If you see a response, Nova is working.

---

## 6. Hackathon Resources (from Devpost)

| Resource | URL |
|----------|-----|
| **Nova Code Examples** | https://docs.aws.amazon.com/nova/latest/userguide/code-examples.html |
| **Nova 2 Lite Blog** | https://aws.amazon.com/blogs/aws/introducing-amazon-nova-2-lite-a-fast-cost-effective-reasoning-model/ |
| **Bedrock API Keys** | https://aws.amazon.com/blogs/machine-learning/accelerate-ai-development-with-amazon-bedrock-api-keys/ |
| **Office Hours** | Wed 8:00am PT — [Resources](https://amazon-nova.devpost.com/resources) |
| **Discord** | #tech-questions for support |

---

## 7. Troubleshooting

| Error | Fix |
|------|-----|
| **`ValidationException: Operation not allowed`** | **→ Tu dois activer l'accès aux modèles.** Voir section 7.1 ci-dessous. |
| `AccessDeniedException` | Wait 2–15 min after first use; check IAM/API key permissions |
| `ValidationException` (autre) | Verify model ID: `amazon.nova-2-lite-v1:0` |
| `ExpiredToken` | Refresh API key or IAM credentials |
| No payment method | Add a card in **Billing** → **Payment methods** |

### 7.1 "Operation not allowed" — Pistes de résolution

Les modèles serverless sont désormais auto-activés au premier appel. Si tu vois "Operation not allowed" :

1. **Essaie les credentials IAM** au lieu de la clé API — crée un IAM user avec `AmazonBedrockFullAccess`, génère une access key, et mets dans `.env` :
   ```
   AWS_ACCESS_KEY_ID=AKIA...
   AWS_SECRET_ACCESS_KEY=...
   AWS_DEFAULT_REGION=us-east-1
   ```
   (Retire ou commente `AWS_BEARER_TOKEN_BEDROCK` pour forcer l'usage IAM)

2. **Vérifie la région** — utilise **us-east-1** (US East N. Virginia)

3. **Modèles Anthropic** — pour Claude, les premiers utilisateurs peuvent devoir soumettre un formulaire use case dans la console Bedrock

4. **Contact AWS Support** — si tout échoue, il peut y avoir des restrictions compte-spécifiques

---

*For NAVIGUIDE: once credentials are in `naviguide_workspace/.env`, the orchestrator loads them at startup. Nova 2 Lite is tried first; Claude 3.5 Sonnet is the fallback if Nova fails.*
