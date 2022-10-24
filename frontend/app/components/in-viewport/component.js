import Component from "@ember/component";

const InViewportComponent = Component.extend({
  rootSelector: "body",
  rootMargin: 0,

  _observer: null,

  didInsertElement(...args) {
    this._super(...args);

    const observer = new IntersectionObserver(
      ([{ isIntersecting }]) => {
        if (isIntersecting) {
          return this.getWithDefault("on-enter-viewport", () => {})();
        }

        return this.getWithDefault("on-exit-viewport", () => {})();
      },
      {
        root: document.querySelector(this.rootSelector),
        rootMargin: `${this.rootMargin}px`
      }
    );

    this.set("_observer", observer);

    // eslint-disable-next-line ember/no-observers
    observer.observe(this.element);
  },

  willDestroyElement(...args) {
    this._super(...args);

    this._observer.disconnect();
  }
});

export default InViewportComponent;
