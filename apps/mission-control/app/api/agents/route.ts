import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';
import matter from 'gray-matter';

const AGENTS_DIR = path.join(process.env.HOME || '', '.claude', 'agents');

export async function GET() {
  try {
    const files = fs.readdirSync(AGENTS_DIR).filter(f => f.endsWith('.md') && !f.startsWith('--'));
    const agents = files.map(file => {
      const content = fs.readFileSync(path.join(AGENTS_DIR, file), 'utf-8');
      const { data: frontmatter } = matter(content);
      return {
        id: file.replace('.md', ''),
        name: frontmatter.name || file.replace('.md', ''),
        description: frontmatter.description || '',
        model: frontmatter.model || 'opus',
        tools: frontmatter.tools || '',
      };
    });
    return NextResponse.json(agents);
  } catch {
    return NextResponse.json([], { status: 200 });
  }
}
