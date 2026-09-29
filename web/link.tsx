import type { AnchorHTMLAttributes, ReactNode } from 'react';
const base = import.meta.env.BASE_URL;
export default function Link({href,children,...props}:{href:string,children:ReactNode}&Omit<AnchorHTMLAttributes<HTMLAnchorElement>,'href'>){
  const page=href==='/'?'overview':href.replace(/^\//,'');
  return <a href={`${base}#${page}`} {...props}>{children}</a>;
}
