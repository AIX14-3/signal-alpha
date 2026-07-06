import run_report_normalize_backfill as cli


def test_parser_defaults_to_dry_run_batch_for_all_stocks():
    args = cli.build_parser().parse_args([])

    assert args.execute is False
    assert args.stock_code is None
    assert args.limit == 100
    assert args.priority == "batch"


def test_parser_accepts_execute_options():
    args = cli.build_parser().parse_args(
        ["--execute", "--stock-code", "005930", "--limit", "25", "--priority", "immediate"]
    )

    assert args.execute is True
    assert args.stock_code == "005930"
    assert args.limit == 25
    assert args.priority == "immediate"
