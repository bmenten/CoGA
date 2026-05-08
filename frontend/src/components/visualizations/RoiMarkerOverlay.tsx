import React from 'react';

interface Props {
  startX: number | null;
  endX: number | null;
  title?: string;
}

const RoiMarkerOverlay: React.FC<Props> = ({
  startX,
  endX,
  title,
}) => {
  if (
    startX === null ||
    endX === null ||
    Number.isNaN(startX) ||
    Number.isNaN(endX)
  ) {
    return null;
  }

  const left = Math.min(startX, endX);
  const width = Math.max(Math.abs(endX - startX), 2);

  return (
    <div
      className="roi-marker-overlay"
      style={{ left, width }}
      title={title}
    >
      <span className="roi-marker-window" />
    </div>
  );
};

export default RoiMarkerOverlay;
