# Testing — Amazon Bedrock AI Inventory Sync

Work through these in order. Step 1 needs no AWS account and no Credo AI tenant, so you can confirm
the cookbook installs and fails cleanly before arranging access to either.

---

## Step 1: Install and smoke-test, no AWS and no tenant

```bash
cd server/python
pip install -r requirements.txt
python main.py --help
```

The help text should name every flag in README Step 5. Then prove the two
"could not start" paths, neither of which needs credentials:

```bash
printf 'BEDROCK_NONSENSE=1\n' >> ../.env     # an unrecognized key
python main.py --dry-run ; echo "exit=$?"    # expect exit=2 naming the key
```

Remove that line afterwards. With a valid `.env` but no AWS credentials
resolvable, the run must also exit `2` with a region or credentials message —
never a traceback.

## Step 2: Confirm AWS access, no Credo AI involved

Prove the credentials and the IAM policy before introducing a tenant.

```bash
python -c "import boto3; print('ok' if boto3.Session().get_credentials() else 'NO CREDENTIALS')"
python -c "import boto3; print(len(boto3.client('bedrock').list_foundation_models()['modelSummaries']), 'foundation models')"
python -c "import boto3; print(boto3.client('bedrock-agentcore-control').list_harnesses())"
```

| Result                               | Meaning                                                                      |
| ------------------------------------ | ---------------------------------------------------------------------------- |
| A model count                        | `bedrock:*` grants are working                                               |
| `{'harnesses': [...]}`               | AgentCore is reachable and the `bedrock-agentcore:` grants are working       |
| `{'harnesses': []}`                  | Reachable, but this account has no agents — the agent half will sync nothing |
| `AccessDeniedException` on harnesses | The `bedrock-agentcore:` actions are missing — see README Step 2             |

If the harness list is empty, create one throwaway harness so the agent path is actually exercised.
A harness that is never invoked costs nothing.

---

## Step 3: Source record

```bash
python ensure_credo_source.py
python ensure_credo_source.py    # again — must report "already exists"
```

Both runs must succeed. Skipping this makes every create in Step 5 fail with a validation error.

---

## Step 4: Dry run — reads only

```bash
python main.py --dry-run -v
```

Confirm:

- Every line is prefixed `[dry-run] would …` and the summary says **planned**, not complete
- Nothing appears in Credo AI afterwards
- The model count matches what Step 2 reported, narrowed to what your agents name unless
  `BEDROCK_SYNC_ALL_FOUNDATION_MODELS=true`

---

## Step 5: Live run

```bash
python main.py -v
```

Confirm all four summary lines appear, and that the run's exit code is `0` (or `1` with an explained
error — see Step 7).

---

## Step 6: Idempotency — the important one

Run it a second time without changing anything in AWS:

```bash
python main.py -v
```

Expect **zero writes**:

```text
models      scanned=N created=0 updated=0 unchanged=N
vendors     providers=N created=0 linked=0 already_linked=N
use cases   scanned=N created=0 updated=0 unchanged=N
model links agents=N linked=0 already_linked=N unresolved=0
```

`created=0` and `linked=0` on the second run is the whole safety argument for scheduling this.

---

## Step 7: Failure modes

Each of these must degrade, not crash. Check the exit code every time.

| Scenario                          | How to trigger                                          | Expected                                                                                         |
| --------------------------------- | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Missing AgentCore permissions     | Remove the `bedrock-agentcore:` actions from the policy | Models still sync; error recorded; exit `1`                                                      |
| Region without `ListCustomModels` | `BEDROCK_REGION=us-east-2`                              | Foundation and imported models still sync; `UnknownOperationException` recorded; exit `1`        |
| Bad Credo AI credentials          | Corrupt `CREDO_API_KEY`                                 | Exit `2`, readable message, no partial writes                                                    |
| Unrecognized `.env` key           | Add `BEDROCK_NONSENSE=1`                                | Exit `2` naming the key, not a traceback                                                         |
| No region resolvable              | Unset `BEDROCK_REGION` and `AWS_DEFAULT_REGION`         | Exit `2` with a region message                                                                   |
| Agents disabled                   | `python main.py --no-agentcore -v`                      | No Use Cases touched; a warning that no foundation models are in scope under the default setting |
| Whole catalog                     | `python main.py --all-foundation-models --dry-run -v`   | Plans the full regional catalog rather than the narrowed set                                     |

---

## Step 8: Confirm in Credo AI

In the Governance App:

1. A synced model shows `Source: Amazon Bedrock` on its record
2. That model is linked to a **Vendor** named after its provider
3. Each agent appears as a **Use Case** whose description carries its model, tools, memory mode,
   iteration cap and system prompt
4. That Use Case lists the **Model** its harness calls

Item 4 is the one worth checking closely: if the agent runs on an inference profile
(`global.…`, `us.…`), the link should resolve to the underlying model rather than reporting
`unresolved`.

---

## Cold-setup test

Hand this cookbook to someone on your team who has never seen it. Time how long it takes them to reach a
successful `python main.py` against their own AWS account, using only `README.md` and this file. Log
every point of confusion.

Watch specifically for the `bedrock-agentcore:` IAM prefix and the `Source` record in Step 3. Both
are easy to miss and both fail in ways that don't obviously name their cause.
