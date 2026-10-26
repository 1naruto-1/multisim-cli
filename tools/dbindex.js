// 读取 Multisim 主元件库（Jet 数据库），输出 {元件名: [组, 族]} 的 JSON。
// 复用 electronics-workbench-decoder 内置的 mdb-reader（npm i -g electronics-workbench-decoder）。
const fs = require("fs"), Module = require("module"), path = require("path");
const [, , dbPath, outPath, pkgDir] = process.argv;
const file = path.join(pkgDir, "dist", "index.cjs");
const mod = new Module(file);
mod.filename = file;
mod.paths = Module._nodeModulePaths(path.dirname(file));
mod._compile(fs.readFileSync(file, "utf8") + "\nmodule.exports.MDBReader = MDBReader;\n", file);
const db = new mod.exports.MDBReader(fs.readFileSync(dbPath));
const groups = {};
for (const r of db.getTable("SYS_CONFIGURATION_GROUPS").getData()) groups[r.Family_Group_ID] = r.Family_Group;
const families = {};
for (const r of db.getTable("SYS_CONFIGURATION").getData({ columns: ["Family_ID", "Family_Name", "Family_Group_ID"] }))
  families[r.Family_ID] = [groups[r.Family_Group_ID], r.Family_Name];
const out = {};
for (const c of db.getTable("SYS_COMPONENT").getData({ columns: ["Family_ID", "Component_Name"] })) {
  const f = families[c.Family_ID];
  if (f && c.Component_Name && !(c.Component_Name in out)) out[c.Component_Name] = f;
}
fs.writeFileSync(outPath, JSON.stringify(out));
console.log(Object.keys(out).length);
