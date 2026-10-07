# Screen v2: training on AWS

Owner's order, 7 Oct 2026: train on the cloud, store on the cloud, keep only the logic
and the main points on the Mac. This page is the plan; `cloud/` is the code that runs it.

Account 695656921622, region ap-south-1, profile `pothole`.

## What exists on AWS

| Resource | Name | Notes |
|---|---|---|
| S3 bucket | `pothole-reporter-ml-695656921622-ap-south-1` | private (all four public-access blocks), SSE-S3, tag `project=pothole-reporter-ml` |
| IAM role and instance profile | `pothole-reporter-ml-trainer` | `AmazonSSMManagedInstanceCore`; inline: read/write on that bucket, `GetSecretValue` on the detector secret only |
| Security group | `pothole-reporter-ml-trainer` | default VPC, no inbound rule |
| EC2 instance | `pothole-ml-trainer` | one at a time, `g4dn.2xlarge` (T4, 8 vCPU, 32 GB), current Deep Learning AMI with PyTorch for Ubuntu 22.04 resolved from SSM, 200 GB gp3 deleted on termination |

The instance has no key pair and no open port. It is driven through SSM
(`cloud/ssm.sh`). Its user data starts `shutdown -h +720` and shutdown means terminate,
so it destroys itself 12 hours after boot whatever happens. The bucket, role and group
stay after a run; `cloud/teardown.sh` terminates the instance and checks nothing tagged
`project=pothole-reporter-ml` is left running or unattached.

## Bucket layout

```
datasets/<name>/raw/         public archives as downloaded (expire after 30 days)
datasets/<name>/receipt.json URL, bytes, sha256, date
frames/<name>/               prepared whole frames, one tar per dataset, and the index
labels/                      teacher answers (one tar) and the manifest
v1-work/                     the v1 working directory from the Mac (private drive frames)
runs/<run-id>/progress.md    one line per finished stage
runs/<run-id>/{logs,checkpoints,report}/
models/<version>/            model.onnx, model.json, parity receipt
```

## Data

Only sets with a clear open licence that download over HTTPS with no account, token or
form (`cloud/fetch_raw.sh`). The model card has the table with URL, licence, counts and
view, and the list of what was refused and why.

## Pipeline (`cloud/run.sh`, on the instance, every stage resumable)

1. `setup`: Python packages, Node 22, `npm ci` for the serving path.
2. `restore_v1`: v1 frames, teacher answers and manifest from `v1-work/`.
3. `fetch`: public archives to local disk, mirrored to `datasets/<name>/raw/`.
4. `prepare`: choose images by their annotations, give each the app's Drive Mode
   preparation (whole frame, never cropped), hash it.
5. `label`: the teacher, same script and production prompt as v1. The key is read from
   the detector secret into memory; it is never printed or written anywhere. Total
   teacher budget USD 40 including the USD 11.51 v1 spent.
6. `manifest`: splits by source. Validation and test never share a source with training.
7. `train`: the v1 recipe on the new data, the same with augmentation, and full
   fine-tunes of MobileNetV3-L and EfficientNet-B0 at 448 with augmentation.
8. `evaluate`: thresholds from validation at 98% and 99% recall, judged on every
   held-out slice, against v1 scored on the same frames. `report.json`, `report.md`.
9. `export`: ONNX, checked against torch on the whole test split, thresholds set on
   serving-path scores. Published to `models/<version>/`.

Each stage ends by syncing its logs and outputs to S3 and appending to
`runs/<run-id>/progress.md`. The driver chains every stage and finishes by itself, so a
lost session costs nothing: read `progress.md` first.

## Gates

The screen Lambda gets the new model only if v2 beats v1 on every held-out slice at the
validation-98% threshold. Shadow mode only. The central stack, its parameters and the
detector secret's contents are never changed.

## Money

Compute cap USD 25 (about 30 instance-hours at about USD 0.8 an hour). Teacher cap
USD 40 in total. Instance hours and spend are in the model card.

## Run it

```bash
ml/classifier/cloud/aws_setup.sh                 # bucket, role, security group (idempotent)
ml/classifier/cloud/launch.sh                    # the one instance
ml/classifier/cloud/ssm.sh 'cd /opt/ml && git clone -b feat/screen-v2 https://github.com/coding-parrot/pothole-reporter.git repo'
ml/classifier/cloud/ssm.sh 'cd /opt/ml/repo && nohup setsid ml/classifier/cloud/run.sh > /opt/ml/run.log 2>&1 &'
aws s3 cp s3://pothole-reporter-ml-695656921622-ap-south-1/runs/<run-id>/progress.md -
ml/classifier/cloud/teardown.sh                  # always, pass or fail
```
