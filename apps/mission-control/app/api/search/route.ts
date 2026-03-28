import { NextRequest, NextResponse } from 'next/server';
import { execSync } from 'child_process';

export async function GET(req: NextRequest) {
  const query = req.nextUrl.searchParams.get('q');
  if (!query || query.trim().length < 2) {
    return NextResponse.json([]);
  }

  try {
    // Shell out to QMD for vault search
    const result = execSync(
      `${process.env.HOME}/.bun/bin/qmd query "${query.replace(/"/g, '\\"')}" -n 8 --json 2>/dev/null`,
      { timeout: 5000, encoding: 'utf-8' }
    );

    const parsed = JSON.parse(result);
    // QMD returns { results: [...] } or an array directly
    const results = Array.isArray(parsed) ? parsed : parsed.results || [];

    return NextResponse.json(
      results.map((r: Record<string, unknown>) => ({
        title: r.title || r.path || 'Untitled',
        path: r.path || '',
        collection: r.collection || '',
        snippet: r.snippet || r.context || '',
        score: r.score || 0,
      }))
    );
  } catch {
    // QMD not available or query failed — return empty
    return NextResponse.json([]);
  }
}
