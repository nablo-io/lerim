"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
	{ href: "/overview", label: "Overview", mobileLabel: "Home", icon: ActivityIcon },
	{ href: "/analytics", label: "Insights", icon: InsightsIcon },
	{ href: "/memory", label: "Briefs", icon: MemoryIcon },
	{ href: "/context", label: "Records", icon: BrainIcon },
	{ href: "/context-graph", label: "Graph", icon: PipelineIcon },
	{ href: "/skills", label: "Skills", icon: SkillsIcon },
	{ href: "/operations", label: "Operations", mobileLabel: "Ops", icon: LogsIcon },
	{ href: "/traces", label: "Sources", mobileLabel: "Sources", icon: TableIcon, secondary: true },
];

export default function Sidebar({
	collapsed,
	onToggleCollapsed,
}: {
	collapsed: boolean;
	onToggleCollapsed: () => void;
}) {
	const pathname = usePathname();

	return (
		<>
			<aside
				data-state={collapsed ? "collapsed" : "expanded"}
				className="dashboard-sidebar fixed inset-y-0 left-0 z-30 hidden grid-rows-[4rem_minmax(0,1fr)_4.75rem] overflow-hidden border-r border-[var(--border)] bg-[var(--bg-subtle)] md:grid"
			>
				{/* Header */}
				<div className="relative h-16 min-w-0 overflow-hidden">
					<button
						type="button"
						onClick={onToggleCollapsed}
						aria-controls="dashboard-sidebar-nav"
						aria-expanded={!collapsed}
						aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
						title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
						className="absolute left-3.5 top-3.5 flex h-9 w-9 items-center justify-center rounded-md border border-[var(--border)] bg-white/[0.03] text-[var(--text-secondary)] transition-colors hover:bg-white/[0.07] hover:text-[var(--text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
					>
						<SidebarToggleIcon collapsed={collapsed} className="h-4 w-4" />
					</button>
					<div
						className={`absolute inset-y-0 left-16 right-0 flex min-w-0 items-center gap-2.5 overflow-hidden pr-3 transition-[opacity,transform] duration-150 ${
							collapsed ? "pointer-events-none translate-x-1 opacity-0" : "translate-x-0 opacity-100"
						}`}
						aria-hidden={collapsed}
					>
						<img src="/lerim-logo.png" alt="Lerim" className="h-7 w-7 object-contain" />
						<span className="min-w-0 truncate text-sm font-semibold tracking-tight text-[var(--text)]">
							Lerim
						</span>
					</div>
				</div>

				{/* Nav */}
				<nav id="dashboard-sidebar-nav" aria-label="Primary" className="min-h-0 min-w-0 overflow-y-auto overflow-x-hidden px-2 pt-2">
					<div className="flex flex-col gap-0.5">
						{NAV_ITEMS.map(({ href, label, icon: Icon, secondary }) => {
							const active = isActiveRoute(pathname, href);
							return (
								<Link
									key={href}
									href={href}
									title={collapsed ? label : undefined}
									aria-current={active ? "page" : undefined}
									className={`grid h-11 grid-cols-[3rem_minmax(0,1fr)] items-center overflow-hidden rounded-md text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)] ${
										active
											? "bg-white/[0.08] text-[var(--text)]"
											: secondary
												? "text-[var(--text-muted)] hover:bg-white/[0.04] hover:text-[var(--text-secondary)]"
												: "text-[var(--text-secondary)] hover:bg-white/[0.04] hover:text-[var(--text)]"
									}`}
								>
									<span aria-hidden="true" className="flex h-11 w-12 items-center justify-center">
										<Icon className="h-4 w-4 shrink-0" />
									</span>
									<span
										className={`min-w-0 truncate transition-[opacity,transform] duration-150 ${
											collapsed ? "pointer-events-none translate-x-1 opacity-0" : "translate-x-0 opacity-100"
										}`}
									>
										{label}
									</span>
								</Link>
							);
						})}
					</div>
				</nav>

				{/* Footer */}
				<div className="min-w-0 border-t border-[var(--border)] px-2 py-3">
					<Link
						href="/settings"
						title={collapsed ? "Settings" : undefined}
						aria-current={pathname === "/settings" ? "page" : undefined}
						className={`grid h-11 grid-cols-[3rem_minmax(0,1fr)] items-center overflow-hidden rounded-md text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)] ${
							pathname === "/settings"
								? "bg-white/[0.08] text-[var(--text)]"
								: "text-[var(--text-secondary)] hover:bg-white/[0.04] hover:text-[var(--text)]"
						}`}
					>
						<span aria-hidden="true" className="flex h-11 w-12 items-center justify-center">
							<GearIcon className="h-4 w-4 shrink-0" />
						</span>
						<span
							className={`min-w-0 truncate transition-[opacity,transform] duration-150 ${
								collapsed ? "pointer-events-none translate-x-1 opacity-0" : "translate-x-0 opacity-100"
							}`}
						>
							Settings
						</span>
					</Link>
				</div>
			</aside>
				<nav aria-label="Mobile primary" className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-5 gap-1 border-t border-[var(--border)] bg-[rgba(17,24,39,0.96)] px-1 pb-[calc(env(safe-area-inset-bottom)+0.5rem)] pt-2 backdrop-blur min-[520px]:grid-cols-9 md:hidden">
				{[...NAV_ITEMS, { href: "/settings", label: "Settings", mobileLabel: "Settings", icon: GearIcon }].map(({ href, label, mobileLabel, icon: Icon }) => {
					const active = isActiveRoute(pathname, href);
					return (
						<Link
							key={href}
							href={href}
							aria-label={label}
							aria-current={active ? "page" : undefined}
							className={`flex min-h-11 min-w-0 flex-col items-center justify-center gap-1 rounded-lg px-1 text-[10px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)] ${
								active
									? "bg-white/[0.08] text-[var(--text)]"
									: "text-[var(--text-secondary)] hover:bg-white/[0.04] hover:text-[var(--text)]"
							}`}
						>
							<span aria-hidden="true"><Icon className="h-4 w-4 shrink-0" /></span>
							<span className="max-w-full truncate">{mobileLabel || label}</span>
						</Link>
					);
				})}
			</nav>
		</>
	);
}

function isActiveRoute(pathname: string, href: string) {
	if (href === "/overview") {
		return pathname === "/" || pathname === "/overview" || pathname.startsWith("/overview/");
	}
	return pathname === href || pathname.startsWith(`${href}/`);
}

/* ---- Inline SVG icon components ----------------------------------- */

function SidebarToggleIcon({ collapsed, className }: { collapsed: boolean; className?: string }) {
	return (
		<svg
			className={className}
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			strokeWidth={2}
			strokeLinecap="round"
			strokeLinejoin="round"
		>
			<path d="M4 5h16v14H4z" />
			<path d="M9 5v14" />
			<path d={collapsed ? "m13 9 3 3-3 3" : "m16 9-3 3 3 3"} />
		</svg>
	);
}

function ActivityIcon({ className }: { className?: string }) {
	return (
		<svg
			className={className}
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			strokeWidth={2}
			strokeLinecap="round"
			strokeLinejoin="round"
		>
			<path d="M2 12h4l3-9 4 18 3-9h4" />
		</svg>
	);
}

function InsightsIcon({ className }: { className?: string }) {
	return (
		<svg
			className={className}
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			strokeWidth={2}
			strokeLinecap="round"
			strokeLinejoin="round"
		>
			<path d="M4 19V5" />
			<path d="M4 19h16" />
			<path d="M8 16v-4" />
			<path d="M12 16V8" />
			<path d="M16 16v-6" />
			<path d="m8 9 3-3 3 2 4-5" />
		</svg>
	);
}

function BrainIcon({ className }: { className?: string }) {
	return (
		<svg
			className={className}
			xmlns="http://www.w3.org/2000/svg"
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			strokeWidth={2}
			strokeLinecap="round"
			strokeLinejoin="round"
		>
			<path d="M12 2a7 7 0 0 0-7 7c0 3 2 5.5 4 7l1.5 1.5h3L15 16c2-1.5 4-4 4-7a7 7 0 0 0-7-7z" />
			<path d="M12 22v-4" />
			<path d="M9 18h6" />
		</svg>
	);
}

function MemoryIcon({ className }: { className?: string }) {
	return (
		<svg
			className={className}
			xmlns="http://www.w3.org/2000/svg"
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			strokeWidth={2}
			strokeLinecap="round"
			strokeLinejoin="round"
		>
			<path d="M5 5a2 2 0 0 1 2-2h11v16H7a2 2 0 0 0-2 2z" />
			<path d="M5 5v16" />
			<path d="M9 7h5" />
			<path d="M9 11h6" />
			<path d="M9 15h4" />
		</svg>
	);
}

function GearIcon({ className }: { className?: string }) {
	return (
		<svg
			className={className}
			xmlns="http://www.w3.org/2000/svg"
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			strokeWidth={2}
			strokeLinecap="round"
			strokeLinejoin="round"
		>
			<circle cx="12" cy="12" r="3" />
			<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1.08-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1.08 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c.26.604.852.997 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
		</svg>
	);
}

function TableIcon({ className }: { className?: string }) {
	return (
		<svg
			className={className}
			xmlns="http://www.w3.org/2000/svg"
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			strokeWidth={2}
			strokeLinecap="round"
			strokeLinejoin="round"
		>
			<path d="M4 5h16v14H4z" />
			<path d="M4 10h16M9 5v14" />
		</svg>
	);
}

function PipelineIcon({ className }: { className?: string }) {
	return (
		<svg
			className={className}
			xmlns="http://www.w3.org/2000/svg"
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			strokeWidth={2}
			strokeLinecap="round"
			strokeLinejoin="round"
		>
			<circle cx="5" cy="12" r="2" />
			<circle cx="12" cy="12" r="2" />
			<circle cx="19" cy="12" r="2" />
			<path d="M7 12h3M14 12h3" />
		</svg>
	);
}

function SkillsIcon({ className }: { className?: string }) {
	return (
		<svg
			className={className}
			xmlns="http://www.w3.org/2000/svg"
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			strokeWidth={2}
			strokeLinecap="round"
			strokeLinejoin="round"
		>
			<path d="M4 6h16" />
			<path d="M4 12h10" />
			<path d="M4 18h7" />
			<path d="m16 17 2 2 4-5" />
		</svg>
	);
}

function LogsIcon({ className }: { className?: string }) {
	return (
		<svg
			className={className}
			xmlns="http://www.w3.org/2000/svg"
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			strokeWidth={2}
			strokeLinecap="round"
			strokeLinejoin="round"
		>
			<path d="M5 4h14v16H5z" />
			<path d="M8 8h8M8 12h8M8 16h5" />
		</svg>
	);
}
