#!/usr/bin/env node
// 从 hsr-data 包中提取中文角色/光锥/遗器/物品/成就数据，供 Python 题库生成脚本使用。
//
// 用法:
//   npm install hsr-data
//   node scripts/extract_hsr_data.mjs --dir node_modules/hsr-data/dist --out /tmp/hsr_cn.json

import fs from "node:fs";
import path from "node:path";

const args = process.argv.slice(2);
function arg(name, fallback) {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : fallback;
}

const distDir = path.resolve(arg("--dir", "node_modules/hsr-data/dist"));
const outPath = path.resolve(arg("--out", "/tmp/hsr_cn.json"));
const categories = ["characters", "lightcones", "relics", "items", "achievements"];

const output = {};
for (const category of categories) {
  const prefix = `data_cn_${category}.min-`;
  const file = fs
    .readdirSync(distDir)
    .find((name) => name.startsWith(prefix) && name.endsWith(".js"));
  if (!file) {
    throw new Error(`missing ${prefix}*.js in ${distDir}`);
  }
  const module = await import(path.join(distDir, file));
  output[category] = module.default;
  console.log(category, output[category].length);
}

fs.writeFileSync(outPath, JSON.stringify(output));
console.log(`wrote ${outPath}`);
