import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const dir=path.dirname(fileURLToPath(import.meta.url));
export default defineConfig({
  base:'/perm-metal-prices-demo/',
  plugins:[react()],
  resolve:{alias:[{find:'next/link',replacement:path.join(dir,'link.tsx')},{find:'@',replacement:path.join(dir,'./shared')}]},
  build:{outDir:'dist',emptyOutDir:true}
});
