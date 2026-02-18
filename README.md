# **Orbit 🪐**  

## 🚀 Overview

Orbit is an AI-based trading framework designed to bridge research experimentation and production trading. The system focuses on combining classical strategies, machine learning models, and reinforcement learning experiments into a modular and automation-friendly architecture.

The project emphasizes:

Market Intelligence

Strategy Research experimentation → Production

Automated Trade execution and monitoring 


## System Architecture

### High-Level Runtime Flow

```mermaid
flowchart LR

    subgraph AI["Market Intelligence Engine"]
        A1[Reddit / News Clients]
        A2[Sentiment Analysis]
        A3[Sentiment Workflow]
        A4[Sentiment]
        A5[Sentiment Market Impact data]
        A6[(MongoDB)]
        A7[(Redis)]
        A1 --> A2 --> A3 --> A4 --> A7
        A3 --> A5 --> A6
    end

    subgraph CORE["Core Engine (Threaded)"]
        C1[Strategy Signal Generation]
        C2[Order Manager]
        C3[Trade SL/TP Monitoring]
        C4[(MongoDB OHLCV)]
        C5[(Redis)]
        C1 --> C2
        C2 --> C3
        C3 <--> C5
        C2 <--> C5
        C1 <--> C4

    end

```


## 🛠️ Getting Started
Prerequisites

Python 3.10+

Linux environment recommended

Redis / MongoDB (optional depending on configuration)

Installation
git clone https://github.com/ipankaj/Orbit.git
cd Orbit
poetry install

Running the Project
poetry run orbit


## 🤝 Contributing

Contributions are welcome. Orbit is a research-driven system, and improvements that enhance:

Stability

Observability

Performance

Research capabilities

Documentation

are highly encouraged.

Please open an issue or submit a pull request with a clear description of changes.


## 🙏 Acknowledgements & Copyright

Orbit is currently in its early development phase. The project is evolving through ongoing research, experimentation, and iteration. Appreciation goes to everyone who shares ideas, feedback, and technical insights that help shape the direction of the system.

This project reflects a continuous learning process and aims to grow into a stable and research-driven trading framework over time.

Copyright © 2026 Pankaj Kumar. All rights reserved
