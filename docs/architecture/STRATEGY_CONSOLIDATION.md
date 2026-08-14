# Strategy consolidation

## Decision

The production strategy surface formerly loaded from the private
`Orbit-Strategies` repository is now part of Orbit. The private repository is a
legacy research archive and no longer participates in installation, CI, Testnet,
or EC2 deployment.

## Migrated production surface

| Symbol | Production class | Orbit module |
|---|---|---|
| BTCUSDT | `SwingStrategyBTC` | `orbit.strategies.swing_strategy` |
| ETHUSDT | `Agglo_ETHERIUM` | `orbit.strategies.agglo_strategy` |
| BCHUSDT | `BollingerAdaptiveReversalStrategyBCH` | `orbit.strategies.reversal_strategy` |

The shared `Strategy` base now contains the ATR and EMA helpers required by
those implementations. `orbit.backtesting` contains the walk-forward validation
engine. Orbit's existing Discord manager and chart utility are reused; the
legacy repository's duplicated notification code and embedded webhook values
were deliberately excluded.

## Excluded legacy surface

- research notebooks and generated datasets
- experimental reinforcement-learning and neural-network programs
- one-off backtesting scripts
- inactive symbol strategies
- duplicated configuration, Discord, and utility modules
- caches, charts, and generated artifacts

## Adding or promoting a strategy

1. Implement the production `Strategy` contract inside `src/orbit/strategies/`.
2. Add its import path to `config/strategies.yaml`.
3. Add deterministic signal tests and walk-forward tests.
4. Validate fees, slippage, drawdown, and out-of-sample behavior.
5. Run the exact Orbit commit on Futures Testnet.
6. Promote that Orbit commit through the guarded live deployment workflow.

This removes cross-repository authentication and version drift: the signal,
risk decision, order, and resulting performance all map to one commit.
