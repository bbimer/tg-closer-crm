from typing import List, Dict, Any
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from src.storage.models import LeadProspect, CrmDialogue, MicroTest

console = Console()


def render_leads_table(leads: List[LeadProspect]) -> None:
    table = Table(title="🎯 Mined Pain Leads & Prospects", show_header=True, header_style="bold cyan")
    table.add_column("User ID", style="dim", width=12)
    table.add_column("Username / Name", style="bold green", width=20)
    table.add_column("Pain Category", style="magenta", width=16)
    table.add_column("Exchange", style="yellow", width=10)
    table.add_column("Segment", style="blue", width=14)
    table.add_column("Quote Sample", style="white")

    for l in leads:
        display_user = f"@{l.username}" if l.username else (l.display_name or str(l.user_id))
        table.add_row(
            str(l.user_id),
            display_user[:20],
            l.pain_category,
            l.exchange_mentioned or "—",
            l.lead_segment.value if hasattr(l.lead_segment, "value") else str(l.lead_segment),
            l.quote_sample[:70] + "..." if len(l.quote_sample) > 70 else l.quote_sample,
        )

    console.print(table)


def render_dialogues_table(dialogues: List[CrmDialogue]) -> None:
    table = Table(title="💬 CRM Active & Historical Dialogues", show_header=True, header_style="bold blue")
    table.add_column("Lead ID", style="dim", width=12)
    table.add_column("Username / Name", style="bold green", width=20)
    table.add_column("Pipeline", style="cyan", width=14)
    table.add_column("Current Stage", style="bold yellow", width=18)
    table.add_column("Status", style="magenta", width=10)
    table.add_column("Drop Reason", style="red", width=22)
    table.add_column("Msgs", style="dim", width=6)

    for d in dialogues:
        display_user = f"@{d.lead_username}" if d.lead_username else (d.lead_name or str(d.lead_user_id))
        table.add_row(
            str(d.lead_user_id),
            display_user[:20],
            d.pipeline_type.value if hasattr(d.pipeline_type, "value") else str(d.pipeline_type),
            d.last_stage_reached,
            d.status.value if hasattr(d.status, "value") else str(d.status),
            d.drop_reason or "—",
            str(d.messages_count),
        )

    console.print(table)


def render_funnel_summary(analytics: Dict[str, Any]) -> None:
    pipeline_type = analytics.get("pipeline_type", "unknown")
    total = analytics.get("total_dialogues", 0)
    won = analytics.get("won", 0)
    lost = analytics.get("lost", 0)
    active = analytics.get("active", 0)
    win_rate = analytics.get("win_rate_percent", 0.0)

    summary_panel = Panel(
        f"[bold]Pipeline:[/bold] {pipeline_type.upper()} | "
        f"[bold]Total Leads:[/bold] {total} | "
        f"[bold green]Won:[/bold green] {won} | "
        f"[bold yellow]Active:[/bold yellow] {active} | "
        f"[bold red]Lost:[/bold red] {lost} | "
        f"[bold cyan]Win Rate:[/bold cyan] {win_rate}%",
        title="📊 Conversion Funnel Overview",
        border_style="cyan",
    )
    console.print(summary_panel)

    steps_table = Table(title="Funnel Stages Breakdown", show_header=True, header_style="bold magenta")
    steps_table.add_column("Stage", style="bold")
    steps_table.add_column("Count", style="cyan", justify="right")

    for step in analytics.get("funnel_steps", []):
        steps_table.add_row(step["stage"], str(step["count"]))

    console.print(steps_table)

    drop_reasons = analytics.get("drop_reasons", {})
    if drop_reasons:
        drop_table = Table(title="⚠️ Drop-Off Bottlenecks", show_header=True, header_style="bold red")
        drop_table.add_column("Drop Reason", style="bold yellow")
        drop_table.add_column("Count", style="red", justify="right")
        for reason, count in drop_reasons.items():
            drop_table.add_row(reason, str(count))
        console.print(drop_table)


def render_micro_tests_table(tests: List[MicroTest]) -> None:
    table = Table(title="🧪 Micro-Tests Telemetry", show_header=True, header_style="bold yellow")
    table.add_column("ID", style="dim", width=6)
    table.add_column("Chat", style="cyan", width=20)
    table.add_column("Format", style="magenta", width=10)
    table.add_column("Alive", style="bold green", width=8)
    table.add_column("Replies", style="yellow", justify="right", width=8)
    table.add_column("Admin Reaction", style="red", width=14)
    table.add_column("Sent Text", style="white")

    for t in tests:
        alive_str = "[green]YES[/green]" if t.is_alive else "[red]DELETED[/red]"
        table.add_row(
            str(t.id),
            (t.chat_title or t.chat_id)[:20],
            t.format_type.value if hasattr(t.format_type, "value") else str(t.format_type),
            alive_str,
            str(t.replies_count),
            t.admin_reaction or "none",
            t.sent_text[:60] + "...",
        )

    console.print(table)
