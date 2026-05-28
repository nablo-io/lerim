"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import { api } from "@/lib/api";
import type { ContextRecord } from "@/lib/types";
import RecordEditor from "@/components/RecordEditor";
import { useToast } from "@/components/Toast";

const GraphExplorer = dynamic(() => import("@/components/GraphExplorer"), {
	ssr: false,
	loading: () => (
		<div className="flex h-full items-center justify-center text-sm text-[var(--text-muted)]">
			Loading graph…
		</div>
	),
});

export default function ContextGraphPage() {
	const { addToast } = useToast();
	const [selectedRecord, setSelectedRecord] = useState<ContextRecord | null>(null);

	async function openRecord(recordId: string) {
		try {
			const record = await api.getRecord(recordId);
			setSelectedRecord(record);
		} catch {
			addToast({ type: "error", message: "Failed to load record" });
		}
	}

	return (
		<>
			<div className="sr-only">
				<div>
					<h1 className="text-lg font-semibold text-[var(--text)]">
						Graph
					</h1>
					<p className="mt-0.5 text-xs text-[var(--text-muted)]">
						Explore clustered records and evidence-backed relationships
					</p>
				</div>
			</div>

			<div className="h-[calc(100vh-6rem)] min-h-[680px] md:fixed md:inset-x-0 md:bottom-0 md:left-[var(--sidebar-width)] md:top-0 md:z-10 md:h-auto md:min-h-0">
				<GraphExplorer onRecordClick={openRecord} />
			</div>

			{selectedRecord && (
				<div
					className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto overscroll-contain bg-black/60 p-4 backdrop-blur-sm"
					onClick={() => setSelectedRecord(null)}
				>
					<div
						role="dialog"
						aria-modal="true"
						aria-labelledby="record-editor-title"
						className="relative max-h-[calc(100vh-2rem)] w-full max-w-2xl overflow-y-auto rounded-xl border border-[var(--border)] bg-[var(--bg-subtle)] p-6 shadow-2xl"
						onClick={(event) => event.stopPropagation()}
					>
						<button
							type="button"
							onClick={() => setSelectedRecord(null)}
							aria-label="Close"
							className="absolute right-4 top-4 flex min-h-11 min-w-11 items-center justify-center rounded-md text-[var(--text-muted)] transition-colors hover:text-[var(--text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
						>
							<svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
								<path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
							</svg>
						</button>
						<RecordEditor
							key={selectedRecord.record_id}
							record={selectedRecord}
						/>
					</div>
				</div>
			)}
		</>
	);
}
