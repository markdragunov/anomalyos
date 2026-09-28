Architecture tests enforce **repository constraints** (layout, invariants, eval ground truth, no product runtime, CI commands). They do not pretend to test detectors, Jev, or ClickHouse.

```bash
python -m unittest discover -s tests -t . -v
python scripts/check_architecture.py
```
