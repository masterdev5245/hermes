# SN SubQuery Hermes Incentive Mechanisms

The SN SubQuery Hermes incentive system evaluates miners through a comprehensive dual scoring mechanism that combines synthetic challenge performance with organic challenge workload. The final score integrates quality assessment across both challenge types to create a balanced incentive structure.

## 1. Synthetic Challenge Scoring

### Factual Accuracy Score (Fact Score)

Synthetic challenges are scored using a two-step process:

**Challenge Generation:**
- Validators generate numerical questions about project schemas using LLM
- Questions focus on specific metrics like counts, sums, averages, or percentages
- Each challenge requires exactly one numerical data point

**Fact Score Extraction:**
- Validators generate ground truth answers using their own Agent (`hermes/validator/challenge_manager.py:274`)
- Miner responses are evaluated using a strict fact-checking LLM prompt (`common/prompt_template.py:108-124`)
- The scoring prompt evaluates factual consistency on a 0-10 scale with one decimal place precision
- Score calculation: `hermes/validator/scorer_manager.py:47-59`

**Scoring Criteria:**
- 0 = completely inconsistent with ground truth
- 10 = perfectly consistent with ground truth
- Evaluation based purely on factual correctness, not style or tone

### Elapsed Time Weight

Response time is weighted quadratically relative to the validator's ground truth generation time:

<p align="center">
  <img src="https://latex.codecogs.com/svg.latex?w%20=%20\frac{1}{\left(1%20+%20\frac{elapsed\_time}{ground\_truth\_cost}\right)^2}" />
</p>

**Implementation:** `common/utils.py:22-31`
- `elapsed_time`: Miner's response time
- `ground_truth_cost`: Validator's ground truth generation time
- Weight decreases quadratically as response time exceeds the baseline

### Synthetic Challenge Final Score

$$
\text{Synthetic Score} = \text{Fact Score} \times \text{Elapsed Time Weight}
$$

**Implementation:** `hermes/validator/scorer_manager.py:42`
```python
zip_scores = [utils.fix_float(s * w) for s, w in zip(ground_truth_scores, elapse_weights)]
```

## 2. Organic Challenge Labor Scoring

### Workload Tracking

Organic challenge labor is tracked using a time-windowed bucket system:

**Bucket Counter System:** `hermes/validator/workload_manager.py:17-69`
- 3-hour sliding window with 1-hour buckets
- Tracks organic challenge completion count per miner
- Automatic cleanup of expired buckets to prevent memory issues

**Labor Collection:** `hermes/validator/workload_manager.py:108-120`
- Each organic challenge completion increments the miner's workload counter
- State persistence and recovery for reliability

### Quality-Workload Balance

Organic challenge scores combine quality EMA with normalized workload:

**Score Calculation:** `hermes/validator/workload_manager.py:139-185`
$$
\text{Workload Score} = \min(0.5 \times \text{Quality EMA} + 0.5 \times \text{Normalized Workload}, 5)
$$

**Components:**
- **Quality EMA**: Exponential moving average of sampled organic challenge scores (α=0.7)
- **Normalized Workload**: `(miner_workload - min_workload) / (max_workload - min_workload)`
- Maximum score capped at 5.0 to prevent excessive weight

## 3. Organic Challenge Sampling System

### Sampling Strategy

Organic challenges are sampled to manage computational overhead while maintaining quality assessment:

**Sampling Configuration:** `hermes/validator/workload_manager.py:100-103`
- `organic_task_sample_rate`: Controls sampling frequency (default: 1 in 5 challenges)
- `organic_task_concurrency`: Number of concurrent organic evaluations (default: 5)
- `organic_task_compute_interval`: Background processing interval (default: 30 seconds)

**Sampling Logic:** `hermes/validator/workload_manager.py:208-212`
```python
miner_uid_work_load = await self.collect(miner_uid, hotkey)
if miner_uid_work_load % self.organic_task_sample_rate != 0:
    logger.debug(f"Skipping organic task computation for miner: {miner_uid} at count {miner_uid_work_load}")
    continue
```

### Quality Score EMA

Sampled organic challenge scores are maintained using exponential moving averages:

**EMA Implementation:** `hermes/validator/ema.py:4-43`
- **Alpha**: 0.7 (weights recent performance more heavily)
- **Deque Length**: Maximum 20 samples per miner
- **Hotkey Change Detection**: Automatic EMA reset when miner hotkey changes

**Quality Score Update:** `hermes/validator/workload_manager.py:232-236`
```python
if miner_uid not in self.uid_sample_scores:
    self.uid_sample_scores[miner_uid] = deque(maxlen=20)
self.uid_sample_scores[miner_uid].append(zip_scores[0])
```

## 4. Overall Score Integration

### EMA Scoring System

The system maintains two separate EMA trackers:

**Synthetic EMA:** `hermes/validator/scorer_manager.py:20-25`
- Tracks synthetic challenge performance across all projects
- Alpha: 0.7, updated each synthetic challenge round

**Overall EMA:** `hermes/validator/scorer_manager.py:72-85`
- Combines synthetic scores and workload scores
- Final score matrix: `synthetic_scores + workload_score`

### Final Score Calculation

$$
\text{Final EMA Score} = \text{EMA}(\text{Synthetic Scores} + \text{Workload Score})
$$

**Implementation:** `hermes/validator/scorer_manager.py:61-86`
- Synthetic challenges contribute project-specific quality scores
- Organic challenges contribute workload and quality assessments
- EMA ensures score stability while rewarding consistent performance

### Weight Setting

Final scores are converted to Bittensor weights every 30 minutes:

**Weight Distribution:** `hermes/validator/challenge_manager.py:335-381`
- Uses `bt.utils.weight_utils.process_weights_for_netuid` for normalization
- Zero-score miners are excluded from weight distribution
- Weights are set on-chain with version key 10010

## Key Implementation Details

### Persistence and Recovery
- Score state persisted to `.data/score_state.pt` (3-day retention)
- Workload state persisted to `.data/workload_state.pt` (3-day retention)
- Automatic recovery on validator restart

### Quality Controls
- Ground truth validation before scoring (`utils.is_ground_truth_valid`)
- Retry logic for failed challenge generation (max 3 attempts)
- Comprehensive error handling and logging

### Performance Optimizations
- Parallel processing of miner queries
- Background organic task evaluation
- Efficient bucket-based workload counting
- EMA-based score smoothing to reduce volatility
