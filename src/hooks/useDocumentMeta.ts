import { useEffect } from "react";

/** Sets the document title (and optional meta description) for the current page. */
export function useDocumentMeta(title: string, description?: string) {
  useEffect(() => {
    document.title = title;

    if (!description) return;

    let tag = document.querySelector('meta[name="description"]');
    if (!tag) {
      tag = document.createElement("meta");
      tag.setAttribute("name", "description");
      document.head.appendChild(tag);
    }
    tag.setAttribute("content", description);
  }, [title, description]);
}
