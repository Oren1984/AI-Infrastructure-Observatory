# Data sources

All data ships as local, versioned YAML fixtures. The demo needs no live API or paid service. Every dataset, KPI and milestone carries one classification:

- **published**: taken from an official publication, with a verification status
- **assumption**: chosen for the fictional scenario
- **simulated**: produced by the engine at run time

The machine-readable catalog is `data/catalog/sources.yaml`. The app's **Evidence catalog** tab and **Explain a number** panel read from it.

## Verification (catalog date 2026-11-10)

| Status | Meaning | Sources |
|---|---|---|
| verified_primary | Primary text retrieved, cited section read | CSO data centre electricity 2023; EU 2024/1364; EU 2023/1791; EU 2024/1689; EU 2022/2555; EU 2015/2193; 40 CFR 60.4211; NIST AI RMF; DOE "Islanding a Microgrid" |
| partially_verified | Official page retrieved; detail not fully visible | AU SOCI Act (section headings only); AU NGER scheme (thresholds not shown); IEA *Energy and AI* (title/date only); LBNL 2024 report (title/date only) |
| secondary_only | Only secondary sources reachable: **gap** | Japan data-centre PUE benchmark; Israel Privacy Law Amendment 13; Ireland CRU Large Energy Users connection decision |
| not_verified | Official source not retrievable: **gap** | NABERS data centres (timeout); METI AI Guidelines (HTTP 403); Israel AI policy PDF (scanned, unreadable); Claude Dashboards video (design inspiration only) |

EU legal texts were retrieved from the EU Publications Office (CELLAR) because EUR-Lex returned an empty bot-check response. The US text was retrieved through the eCFR API. No figure from IEA or LBNL is used numerically.

## Published values used

| Value | Unit | Source |
|---|---|---|
| Data centres' share of Irish metered electricity: 5 (2015), 18 (2022), 21 (2023) | % | CSO, release of 23 July 2024 |
| PUE = E_DC / E_IT; WUE = W_IN / E_IT (W_IN in m³, E_IT in MWh) | ratio; m³/MWh = L/kWh | EU 2024/1364, Annex III |

## Units

- **MW** is power, a rate. **MWh** is energy, an amount. Step length Δt = 1 min = 1/60 h.
- Regional chart: GW. Facility charts: MW. Storage and fuel: % of rated energy or tank, plus battery MWh.
- Emissions: t CO₂ for greenhouse gas, kg NOx for local air pollution. Water: m³ (cumulative).
- Money: EUR, 2025 nominal, excluding VAT.

## Formulas

| Metric | Formula |
|---|---|
| Facility load | IT + IT × 0.22 (cooling) + non-IT critical load |
| PUE (run window) | Σ facility energy ÷ Σ IT energy |
| WUE (run window) | Σ direct water (m³) ÷ Σ IT energy (MWh) |
| Grid emissions | Σ grid import MWh × Σ_i share_i × EF_i |
| Generator emissions | Σ litres × 2.68 kg CO₂/L |
| Generator NOx | Σ generator MWh × 6 g/kWh |
| Direct water | Σ IT kWh × 0.25 L/kWh |
| Generation water | Σ grid MWh × share_i × water factor_i; hydro is *unknown* and excluded |
| Battery SoC | SoC − P·Δt / E_rated, floored at 0 |
| Fuel | fuel − P·Δt × 270 L/MWh, floored at 0 |
| Latency | base × (1 + 0.2ρ/(1−ρ)) + queue / capacity |
| Electricity cost | Σ grid MWh × modelled marginal price |
| Fuel / water / maintenance cost | litres × 1.45 EUR; m³ × 2.80 EUR; run-hours × 135 EUR |

## Calculation boundaries

- **Grid electricity emissions**: location-based, combustion only, for imported electricity (including battery and UPS recharging). **Generator emissions** are counted separately as on-site combustion. The two are never added to the same line, which avoids double counting.
- **Greenhouse gas vs local air pollution**: CO₂ and NOx are reported in separate rows.
- **Water**: only *consumption* is modelled. **Withdrawal** is shown as *Unknown*. Hydro reservoir evaporation is unknown and excluded, and the UI flags it.
- **Costs**: energy component only. Network, capacity and carbon charges are excluded. Capital and annual fixed costs are illustrative category totals.
- **Community effects**: only the demand-reduction minutes and the NOx estimate are shown. Ambient air quality and noise are explicit unknowns.

## Scenario assumptions (selected)

| Item | Value |
|---|---|
| Grid connection / firm import | 34 MW / 20 MW |
| Cooling capacity / cooling power factor | 30 MW IT heat / 0.22 |
| Battery | 6 MW / 6 MWh, 90% initial, 20% reserve |
| Generators | 3 × 6 MW, 2-step start, 4,000 L, 270 L/MWh |
| UPS | 35 MW / 1.0 MWh, bridging only |
| Emission factors (t CO₂/MWh) | gas 0.40, oil 0.75, renewables 0 |
| Water factors (L/MWh, consumption) | gas 750, oil 1,100, wind/solar 0, hydro unknown |
| Regional capacities (MW) | wind 4,800, solar 1,500, hydro 230, gas 4,300, oil 650; demand 5,200 |

These values are illustrative orders of magnitude chosen to make the three states legible. They are not estimates for the Irish power system or for any real site.

## Known limitations

- One-minute steps. Electrical transients, frequency, protection and generator synchronisation are not modelled.
- The regional grid is a five-source stand-in. Interconnectors, coal/peat, pumped-storage cycling and unit commitment are out of scope.
- The service model is a deterministic queueing approximation, not a measured trace.
- The PUE and WUE windows cover 4 hours, not the annual reporting period the regulation defines.
- Gaps listed above are not filled in. Where an official source could not be verified, the catalog says so instead of inferring an obligation or a value.
