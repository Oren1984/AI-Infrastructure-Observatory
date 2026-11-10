# Scenarios

All three scenarios use the same facility, the same request and the same chat workload profile: 40 req/s baseline with a ±10% swing, 240 one-minute steps from 15:00, and a comparison probe at T+100. Any difference between them comes from the operating conditions alone. Inputs are in `data/scenarios/*.yaml`; thresholds are in `data/reference_facility.yaml` → `facility.thresholds`.

## The three operating states

| State | Set when |
|---|---|
| **Normal** | Grid-connected, no demand-reduction instruction, nothing shed, cooling headroom ≥ 10%. |
| **Capacity constrained** | Grid-connected, and a demand-reduction instruction is active, or any load is shed or reduced, or cooling headroom is below 10%. |
| **Emergency** | Islanded, or reconnected after an island while staged recovery is still under way. |

Within these states, the **phase** names the specific situation: Grid-connected, Demand reduction instruction, Load shedding, UPS bridging / generator starting, Islanded on generator, Islanded on battery, Controlled shutdown, Blackout, Staged recovery.

### Normal (`normal.yaml`)
Wind and solar follow deterministic profiles and gas fills the remainder in merit order. At T+120 regional demand rises 4%, which changes the mix, price and emissions. Every workload runs, and the chat is served by the large model.

### Capacity constrained (`constrained.yaml`)
| Step | Input |
|---|---|
| 40 | Gas availability falls to 60% (unit trip) and wind to 50% (lull). The regional margin goes negative, and the grid operator caps the facility's import at the 20 MW firm level. |
| 80 | Chiller fault: cooling capacity falls to 17 MW of IT heat. |
| 140 | Chiller repaired. |
| 150 | Gas and wind return. The instruction is lifted once the margin reaches 8%. |

Training is shed. Batch, optional work and interactive inference keep running within the cap and the reduced cooling capacity. Workloads come back one stage at a time.

### Emergency (`emergency.yaml`): illustrative cascade
| Step | What happens | Engine response |
|---|---|---|
| 20 | Half of gas capacity falls out | Margin < 3%: demand-reduction instruction; training shed |
| 35 | Oil-derived peakers unavailable | `UNAVAILABLE` reason code |
| 50 | Hydro unavailable | `UNAVAILABLE` |
| 60 | Wind at cut-out | `UNAVAILABLE` |
| 70 | Solar lost; regional available/demand < 0.45 | **Grid loss → island.** Grid sources become `UNREACHABLE`; discretionary work is paused; the UPS bridges; generators start |
| 72 | Generators online (2-step start delay) | Islanded on generator (18 MW); chat on the large model |
| 139 | Fuel exhausted (4,000 L) | Generator `EXHAUSTED`; battery carries 6 MW; large-model capacity (17.5 req/s) falls below the routing threshold (95% of demand), so chat is **routed to the smaller model** |
| 184 | Battery reaches 20% reserve | **Controlled shutdown**: serving nodes and accelerators are shut down; core services stay on the reserve; users get an explicit *unavailable* answer |
| 200 | Regional sources restored | Reconnection requires a margin ≥ 5% for 5 steps |
| 204 | Grid reconnected | Staged recovery begins |
| 207–223 | Restoration in priority order | critical chain (207, small model) → interactive full pool (211, back to the large model) → optional → batch → training, each stage after 4 stable steps |

Before islanding, gas, oil, hydro, wind and solar can all contribute to the modelled grid mix. After islanding, only assets configured as electrically reachable and island-capable may supply the facility. The on-site solar canopy qualifies but has no output at night or under heavy cloud. The private-wire wind farm is reachable but grid-following, so its reason code is `NOT_ISLAND_CAPABLE` when it has output.

## Dispatch rules

**Regional grid.** Sources are dispatched in merit order (wind 0, solar 0, hydro 8, gas 105, oil 260 EUR/MWh) up to their available capacity. The price is the marginal cost of the last unit dispatched, or the scarcity price of 1,000 EUR/MWh when demand cannot be met. The facility's grid import is attributed to sources by their regional share.

**Facility budget per step.**
- Grid-connected: `import cap + usable local sources + battery above reserve (grid support allowed)`. Generators are `POLICY_RESTRICTED` while grid-connected (emergency use only).
- Islanded: `usable local sources + generator (when running, limited by fuel) + battery above reserve + UPS (only while the generators start)`. Battery energy below the reserve state of charge may serve **core** load only.
- Every asset is capped by power (MW) and by the energy it can deliver this step (MWh ÷ Δt), so storage and fuel never go below zero.
- Supply order. Grid-connected: local sources → grid → battery. Islanded: local sources → generator → battery → UPS → battery reserve. Spare import recharges the UPS, then the battery.

**Reason codes**: `DISPATCHED`, `AT_CAPACITY`, `STANDBY`, `UNAVAILABLE`, `EXHAUSTED`, `UNREACHABLE`, `NOT_ISLAND_CAPABLE`, `POLICY_RESTRICTED`, `STARTING`, `RESERVE_HELD`. An event is recorded whenever a source's code changes.

## Critical-load order

Power is allocated in this order and shed in reverse:

| # | Block | MW (IT unless noted) | Tier |
|---|---|---|---|
| 1 | Power distribution & controls | 0.6 (non-IT) | core |
| 2 | Minimum observability | 0.15 | core |
| 3 | Network & security services | 0.5 | core |
| 4 | Required storage | 0.4 | core |
| 5 | Serving nodes | 0.6 | inference |
| 6 | Accelerators & GPU memory (minimum pool) | 1.8 | inference |
| 7 | Interactive inference, full pool | 7.0 (fractional) | workload |
| 8 | Optional work | 1.5 | workload |
| 9 | Batch inference | 4.0 | workload |
| 10 | Training | 9.0 | workload |

Essential cooling is part of every IT block's cost: 0.22 MW of cooling per MW of IT. The cooling plant also caps total IT heat. The critical chain is strict: if one link cannot be powered, every later link and all workloads are off. **Inference is gated** on links 1–6. The critical budget is 4.81 MW, of which 1.88 MW is core. With zero capacity everything is off and the state is reported explicitly.

## AI service

`replicas = floor(accelerator MW ÷ replica MW)`. The large model uses 0.35 MW and serves 2.5 req/s per replica; the small model uses 0.15 MW and serves 3.0 req/s. If large-model capacity is below 95% of demand, chat is routed to the small model. The queue carries backlog between steps (maximum 3,000; overflow is rejected). Mean latency is `base × (1 + 0.2ρ/(1−ρ)) + queue/capacity`. Quality is DEGRADED when latency exceeds the 4 s SLO or requests are rejected, REDUCED on the small model, and UNAVAILABLE when the chain is down.

## Thresholds (`facility.thresholds`)

| Threshold | Value |
|---|---|
| Demand-reduction trigger / release margin | 3% / 8% |
| Grid collapse (available ÷ demand) | 0.45 |
| Reconnect margin / stable steps | 5% / 5 |
| Restore stable steps per stage | 4 |
| Cooling headroom alert | 10% |
| Battery low SoC alert / reserve | 30% / 20% |
| Fuel low alert | 20% |
| Latency alert / queue alert | 4 s / 500 requests |

## Recovery

Alerts are raised once while their condition holds and are closed by one `recovery` event. Restoration is staged: the critical chain comes back as one stage, then each workload, in priority order. A stage is restored only after it would have fitted for `restore_stable_steps` consecutive steps, and a lower stage never jumps ahead of a higher one that is still waiting. Every shed, route and restore is logged with an owner, a policy and the event that triggered it.
