import React from 'react';

interface ResultsPaginationProps {
  page: number;
  totalPages: number;
  onPageChange: (nextPage: number) => void;
}

export default function ResultsPagination({
  page,
  totalPages,
  onPageChange,
}: ResultsPaginationProps) {
  return (
    <div className="pagination-row">
      <button
        disabled={page === 1}
        onClick={() => onPageChange(Math.max(1, page - 1))}
        className="button-secondary"
      >
        Prev
      </button>
      <span>
        Page {page} of {totalPages}
      </span>
      <button
        disabled={page >= totalPages}
        onClick={() => onPageChange(Math.min(totalPages, page + 1))}
        className="button-secondary"
      >
        Next
      </button>
    </div>
  );
}
