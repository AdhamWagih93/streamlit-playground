export function Spinner() {
  return <div className="spinner" />;
}

export function SpinnerCenter() {
  return (
    <div className="spinner-center">
      <Spinner />
    </div>
  );
}
