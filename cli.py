import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.storage.models import (
    PipelineType,
    DialogueStatus,
    FastTrackStage,
    DeltaNeutralStage,
    MicroTestFormat,
    LeadSegment,
)
from src.storage.repository import Repository
from src.collector.session_pool import SessionPool
from src.intel.chat_harvester import ChatHarvester
from src.intel.pain_miner import PainMiner
from src.intel.micro_tester import MicroTester
from src.crm.dm_sync import DmSync
from src.crm.pipeline_tracker import PipelineTracker, VALID_DROP_REASONS
from rich.table import Table
from rich import box
from src.utils.formatting import (
    render_leads_table,
    render_dialogues_table,
    render_funnel_summary,
    render_micro_tests_table,
    console,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("closer-crm-cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="closer-cli",
        description="Fast Track Closer & Community Intelligence CRM Engine (v2.0)",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Command: init-db
    subparsers.add_parser("init-db", help="Initialize CRM and Intelligence SQLite tables")

    # Command: harvest
    harvest_p = subparsers.add_parser("harvest", help="Harvest raw messages from a Telegram group")
    harvest_p.add_argument("--chat", required=True, help="Telegram group username, link or ID")
    harvest_p.add_argument("--limit", type=int, default=300, help="Total messages to dump")
    harvest_p.add_argument("--batch", type=int, default=100, help="Batch size per request")
    harvest_p.add_argument("--offset-id", type=int, default=0, help="Offset message ID to start pagination from")

    # Command: mine-pains
    mine_p = subparsers.add_parser("mine-pains", help="Mine pain points and trading frustrations from chat dumps")
    mine_p.add_argument("--chat", required=True, help="Chat ID or entity to mine")
    mine_p.add_argument("--limit", type=int, default=1000, help="Messages to scan from database")

    # Command: digest (Active discussion radar)
    digest_p = subparsers.add_parser("digest", help="Radar of active reply threads and topics in chat dump")
    digest_p.add_argument("--chat", required=True, help="Chat ID or username to inspect")
    digest_p.add_argument("--hours", type=int, default=24, help="Time window in hours (default: 24)")
    digest_p.add_argument("--limit", type=int, default=5, help="Number of top threads to display")

    # Command: leads
    leads_p = subparsers.add_parser("leads", help="View mined pain leads & prospects")
    leads_p.add_argument("--segment", choices=["clean_retail", "whale_arbitrage"], default=None)
    leads_p.add_argument("--pain", default=None, help="Filter by pain category")
    leads_p.add_argument("--status", default=None, help="Filter by status (new, contacted, etc.)")
    leads_p.add_argument("--limit", type=int, default=50)

    # Command: export-lead (handoff to syndicate-kb-responder)
    exp_p = subparsers.add_parser("export-lead", help="Export lead context JSON for syndicate-kb-responder")
    exp_p.add_argument("--lead", type=int, required=True, help="User ID of the lead")

    # Command: export-thread (handoff active dispute thread to syndicate-kb-responder)
    exp_thread_p = subparsers.add_parser("export-thread", help="Export active thread chain and chat slang for syndicate-kb-responder")
    exp_thread_p.add_argument("--chat", required=True, help="Chat ID or username")
    exp_thread_p.add_argument("--msg", type=int, required=True, help="Root or reply message ID")
    exp_thread_p.add_argument("--max-msgs", type=int, default=6, help="Maximum messages in thread chain (default: 6)")

    # Command: import-audits
    imp_p = subparsers.add_parser("import-audits", help="Import scored channels from audits.db")
    imp_p.add_argument("--path", default=None, help="Path to source audits.db")

    # Command: sync-dms
    sync_p = subparsers.add_parser("sync-dms", help="Synchronize personal DMs into the CRM")
    sync_p.add_argument("--limit", type=int, default=40, help="Number of personal dialogs to scan")
    sync_p.add_argument("--msgs", type=int, default=25, help="Messages per dialog to inspect")

    # Command: pipeline
    pipe_p = subparsers.add_parser("pipeline", help="Manage conversion pipelines and stage transitions")
    pipe_sub = pipe_p.add_subparsers(dest="pipe_cmd", help="Pipeline action")

    pipe_list = pipe_sub.add_parser("list", help="List dialogues in pipeline")
    pipe_list.add_argument("--type", choices=["fast_track", "delta_neutral"], default="fast_track")
    pipe_list.add_argument("--status", choices=["active", "won", "lost"], default=None)

    pipe_adv = pipe_sub.add_parser("advance", help="Advance a lead to the next stage")
    pipe_adv.add_argument("--lead", type=int, required=True, help="Lead User ID")
    pipe_adv.add_argument("--stage", required=True, help="Target Stage (e.g. Test_20, Passed, Volume_500_2k)")
    pipe_adv.add_argument("--type", choices=["fast_track", "delta_neutral"], default="fast_track")
    pipe_adv.add_argument("--note", default="", help="Note on this stage transition")

    pipe_drop = pipe_sub.add_parser("drop", help="Mark lead as Lost with a specific bottleneck reason")
    pipe_drop.add_argument("--lead", type=int, required=True, help="Lead User ID")
    pipe_drop.add_argument("--reason", required=True, choices=VALID_DROP_REASONS, help="Drop reason")
    pipe_drop.add_argument("--type", choices=["fast_track", "delta_neutral"], default="fast_track")
    pipe_drop.add_argument("--note", default="", help="Additional context")

    pipe_close = pipe_sub.add_parser("close", help="Close deal as WON")
    pipe_close.add_argument("--lead", type=int, required=True, help="Lead User ID")
    pipe_close.add_argument("--type", choices=["fast_track", "delta_neutral"], default="fast_track")
    pipe_close.add_argument("--note", default="Closed on target volume")

    pipe_stats = pipe_sub.add_parser("stats", help="Show conversion funnel analytics and drop-off stats")
    pipe_stats.add_argument("--type", choices=["fast_track", "delta_neutral"], default="fast_track")

    # Command: micro-test
    micro_p = subparsers.add_parser("micro-test", help="Manage A/B/C micro-test probe messages")
    micro_sub = micro_p.add_subparsers(dest="micro_cmd", help="Micro-test action")

    micro_send = micro_sub.add_parser("send", help="Send a probe message")
    micro_send.add_argument("--chat", required=True, help="Chat identifier")
    micro_send.add_argument("--format", required=True, choices=["Format_A", "Format_B", "Format_C"])
    micro_send.add_argument("--text", default=None, help="Custom message text")
    micro_send.add_argument("--reply-to", type=int, default=None, help="Message ID to reply to")

    micro_check = micro_sub.add_parser("check", help="Check probe message telemetry")
    micro_check.add_argument("--id", type=int, required=True, help="Micro-test record ID")

    micro_list = micro_sub.add_parser("list", help="List all sent probe tests")
    micro_list.add_argument("--chat", default=None, help="Filter by chat ID")

    return parser


async def main_async() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    repo = Repository()

    if args.command == "init-db":
        console.print("[bold green]Initializing database tables...[/bold green]")
        await repo.init_db()
        console.print(f"[bold green]Done! Database ready at: {settings.crm_db_path}[/bold green]")
        return

    # Ensure tables exist
    await repo.init_db()

    if args.command == "harvest":
        session_pool = SessionPool()
        harvester = ChatHarvester(repo, session_pool)
        console.print(f"[bold cyan]Harvesting messages from {args.chat}...[/bold cyan]")
        try:
            result = await harvester.harvest_chat(
                chat_identifier=args.chat,
                total_limit=args.limit,
                batch_size=args.batch,
                start_offset_id=args.offset_id,
            )
            console.print("[bold green]Harvesting completed successfully![/bold green]")
            console.print(result)
        finally:
            await session_pool.close_all()

    elif args.command == "mine-pains":
        miner = PainMiner(repo)
        console.print(f"[bold magenta]Mining pains for chat {args.chat}...[/bold magenta]")
        leads = await miner.mine_chat(chat_id=args.chat, limit=args.limit)
        if leads:
            render_leads_table(leads)
        else:
            console.print("[yellow]No leads matching pain criteria found in this chat dump.[/yellow]")

    elif args.command == "digest":
        threads = await repo.get_active_threads(args.chat, hours=args.hours, limit=args.limit)
        if not threads:
            console.print(f"[yellow]No active reply threads found for chat {args.chat} in the last {args.hours} hours.[/yellow]")
            return

        table = Table(title=f"📡 Active Discussion Threads: {args.chat} (Window: {args.hours}h)", box=box.ROUNDED)
        table.add_column("Msg ID", style="cyan", no_wrap=True)
        table.add_column("Author", style="bold green", no_wrap=True)
        table.add_column("Root Topic / Post", style="white")
        table.add_column("Replies", justify="center", style="bold yellow", no_wrap=True)
        table.add_column("Tickers", style="magenta", no_wrap=True)
        table.add_column("Latest Reply", style="dim")

        for t in threads:
            tickers_str = ", ".join(t["tickers"]) if t["tickers"] else "—"
            latest_str = f"{t['latest_reply_author']}: {t['latest_reply_text']}"
            table.add_row(
                str(t["root_msg_id"]),
                t["root_author"],
                t["root_text"],
                str(t["reply_count"]),
                tickers_str,
                latest_str,
            )

        console.print(table)

    elif args.command == "leads":
        leads = await repo.get_leads(
            segment=args.segment,
            pain_category=args.pain,
            status=args.status,
            limit=args.limit,
        )
        if leads:
            render_leads_table(leads)
        else:
            console.print("[yellow]No leads found matching query.[/yellow]")

    elif args.command == "export-lead":
        lead_prospect = await repo.get_lead(args.lead)
        dialogue = await repo.get_dialogue(args.lead, PipelineType.FAST_TRACK)
        if not dialogue:
            dialogue = await repo.get_dialogue(args.lead, PipelineType.DELTA_NEUTRAL)

        if not lead_prospect and not dialogue:
            console.print(f"[bold red]Lead {args.lead} not found in database.[/bold red]")
            return

        import json
        payload = {
            "user_id": args.lead,
            "username": (dialogue.lead_username if dialogue else None) or (lead_prospect.username if lead_prospect else None),
            "display_name": (dialogue.lead_name if dialogue else None) or (lead_prospect.display_name if lead_prospect else None),
            "source_chat_id": (lead_prospect.source_chat_id if lead_prospect else None) or (dialogue.source_chat_id if dialogue else None),
            "source_chat_title": lead_prospect.source_chat_title if lead_prospect else None,
            "pain_category": lead_prospect.pain_category if lead_prospect else "general",
            "matched_module": (lead_prospect.matched_module if lead_prospect else None) or (dialogue.matched_module if dialogue else "M01_TOKENOMICS_MEV"),
            "deposit_est": (lead_prospect.deposit_est if lead_prospect else None) or (dialogue.deposit_est if dialogue else "500_2000"),
            "quote_sample": lead_prospect.quote_sample if lead_prospect else "",
            "lead_segment": (lead_prospect.lead_segment.value if lead_prospect else "clean_retail"),
            "funnel_recommendation": (dialogue.pipeline_type.value if dialogue else (lead_prospect.funnel_recommendation.value if lead_prospect else "fast_track")),
            "current_stage": dialogue.last_stage_reached if dialogue else "In_DM",
            "status": dialogue.status.value if dialogue else "new",
            "drop_reason": dialogue.drop_reason if dialogue else None,
            "notes": dialogue.notes if dialogue else "",
            "history": json.loads(dialogue.history_json) if dialogue and dialogue.history_json else [],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    elif args.command == "export-thread":
        thread_data = await repo.get_thread_messages(args.chat, args.msg, max_messages=args.max_msgs)
        top_slang = await repo.get_chat_top_slang(args.chat, limit=15)
        thread_data["top_slang"] = top_slang
        import json
        print(json.dumps(thread_data, ensure_ascii=False, indent=2))

    elif args.command == "import-audits":
        from pathlib import Path
        custom_path = Path(args.path) if args.path else None
        count = await repo.import_channel_audits(custom_path)
        console.print(f"[bold green]Imported {count} channel audit records.[/bold green]")

    elif args.command == "sync-dms":
        session_pool = SessionPool()
        syncer = DmSync(repo, session_pool)
        console.print("[bold cyan]Syncing personal DMs into CRM...[/bold cyan]")
        try:
            dialogues = await syncer.sync_dialogs(
                limit_dialogs=args.limit,
                messages_per_dialog=args.msgs,
            )
            render_dialogues_table(dialogues)
        finally:
            await session_pool.close_all()

    elif args.command == "pipeline":
        tracker = PipelineTracker(repo)
        ptype = PipelineType(args.type)

        if args.pipe_cmd == "list":
            dialogues = await repo.get_dialogues_by_pipeline(ptype, status=args.status)
            if dialogues:
                render_dialogues_table(dialogues)
            else:
                console.print(f"[yellow]No dialogues found for pipeline '{ptype.value}'.[/yellow]")

        elif args.pipe_cmd == "advance":
            success = await tracker.advance_stage(
                lead_user_id=args.lead,
                target_stage=args.stage,
                pipeline_type=ptype,
                note=args.note,
            )
            if success:
                console.print(f"[bold green]Lead {args.lead} advanced to '{args.stage}' in {ptype.value}.[/bold green]")
            else:
                console.print(f"[bold red]Failed to advance lead {args.lead}.[/bold red]")

        elif args.pipe_cmd == "drop":
            success = await tracker.drop_lead(
                lead_user_id=args.lead,
                drop_reason=args.reason,
                pipeline_type=ptype,
                note=args.note,
            )
            if success:
                console.print(f"[bold red]Lead {args.lead} dropped. Reason: {args.reason}[/bold red]")
            else:
                console.print(f"[bold red]Failed to drop lead {args.lead}.[/bold red]")

        elif args.pipe_cmd == "close":
            success = await tracker.close_deal(
                lead_user_id=args.lead,
                pipeline_type=ptype,
                note=args.note,
            )
            if success:
                console.print(f"[bold green]Deal CLOSED for lead {args.lead}![/bold green]")
            else:
                console.print(f"[bold red]Failed to close lead {args.lead}.[/bold red]")

        elif args.pipe_cmd == "stats":
            analytics = await tracker.get_funnel_analytics(ptype)
            render_funnel_summary(analytics)

        else:
            parser.parse_args(["pipeline", "--help"])

    elif args.command == "micro-test":
        session_pool = SessionPool()
        tester = MicroTester(repo, session_pool)

        try:
            if args.micro_cmd == "send":
                format_enum = MicroTestFormat(args.format)
                test = await tester.send_test(
                    chat_identifier=args.chat,
                    format_type=format_enum,
                    custom_text=args.text,
                    reply_to_msg_id=args.reply_to,
                )
                console.print(f"[bold green]Micro-test #{test.id} sent to {args.chat}![/bold green]")

            elif args.micro_cmd == "check":
                test = await tester.check_telemetry(args.id)
                if test:
                    render_micro_tests_table([test])
                else:
                    console.print(f"[red]Could not retrieve telemetry for test #{args.id}[/red]")

            elif args.micro_cmd == "list":
                tests = await repo.get_micro_tests(chat_id=args.chat)
                if tests:
                    render_micro_tests_table(tests)
                else:
                    console.print("[yellow]No micro-tests recorded.[/yellow]")
            else:
                parser.parse_args(["micro-test", "--help"])
        finally:
            await session_pool.close_all()


def main() -> None:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
    except Exception as e:
        console.print("[bold red]Error:[/bold red]", str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
