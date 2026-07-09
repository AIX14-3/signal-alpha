import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({ baseDirectory: __dirname });

// Next 15 flat config. `next/core-web-vitals` 로 실제 규칙을 적용해
// `eslint-disable` 주석이 의미를 갖게 한다. CI `verify` 경로(lint=tsc→build→test)는
// 건드리지 않고 `npm run lint:eslint` 로 별도 실행한다(점진 도입).
const eslintConfig = [
  {
    ignores: [".next/**", "node_modules/**", "tests/**", "qa/**", "next-env.d.ts"],
  },
  ...compat.extends("next/core-web-vitals"),
];

export default eslintConfig;
