//! Scroll rail with a usable minimum handle (ported from icedtea).

use iced::advanced::layout::{self, Layout};
use iced::advanced::renderer;
use iced::advanced::widget::tree::{self, Tree};
use iced::advanced::widget::Widget;
use iced::advanced::{Clipboard, Shell};
use iced::mouse;
use iced::widget::scrollable;
use iced::{Background, Color, Element, Event, Length, Rectangle, Size};

use crate::live::{
    scroll_from_rail, scroller_span, SCROLL_HANDLE_MIN, SCROLL_RADIUS, SCROLL_RAIL_WIDTH,
};

#[derive(Default)]
struct State {
    dragging: Option<f32>,
}

/// Vertical rail. `on_scroll` is the content offset (pixels).
pub struct ScrollRail<'a, Message> {
    content: f32,
    viewport: f32,
    scroll: f32,
    on_scroll: Box<dyn Fn(f32) -> Message + 'a>,
}

impl<'a, Message> ScrollRail<'a, Message> {
    pub fn new(
        content: f32,
        viewport: f32,
        scroll: f32,
        on_scroll: impl Fn(f32) -> Message + 'a,
    ) -> Self {
        Self {
            content,
            viewport,
            scroll,
            on_scroll: Box::new(on_scroll),
        }
    }
}

/// Track and thumb colors from iced's default scrollable catalog.
pub fn rail_colors(theme: &iced::Theme, hovered: bool, dragging: bool) -> (Color, Color) {
    let status = if dragging {
        scrollable::Status::Dragged {
            is_horizontal_scrollbar_dragged: false,
            is_vertical_scrollbar_dragged: true,
        }
    } else if hovered {
        scrollable::Status::Hovered {
            is_horizontal_scrollbar_hovered: false,
            is_vertical_scrollbar_hovered: true,
        }
    } else {
        scrollable::Status::Active
    };
    let style = scrollable::default(theme, status);
    let track = match style.vertical_rail.background {
        Some(Background::Color(c)) => c,
        _ => Color::TRANSPARENT,
    };
    (track, style.vertical_rail.scroller.color)
}

fn thumb(content: f32, viewport: f32, scroll: f32, rail: f32) -> (f32, f32) {
    scroller_span(content, viewport, scroll, rail, SCROLL_HANDLE_MIN)
}

impl<Message, Renderer> Widget<Message, iced::Theme, Renderer> for ScrollRail<'_, Message>
where
    Renderer: iced::advanced::Renderer,
{
    fn tag(&self) -> tree::Tag {
        tree::Tag::of::<State>()
    }

    fn state(&self) -> tree::State {
        tree::State::new(State::default())
    }

    fn size(&self) -> Size<Length> {
        Size::new(Length::Fixed(SCROLL_RAIL_WIDTH), Length::Fill)
    }

    fn layout(
        &self,
        _tree: &mut Tree,
        _renderer: &Renderer,
        limits: &layout::Limits,
    ) -> layout::Node {
        let size = limits.resolve(
            Length::Fixed(SCROLL_RAIL_WIDTH),
            Length::Fill,
            Size::new(SCROLL_RAIL_WIDTH, 0.0),
        );
        layout::Node::new(size)
    }

    fn on_event(
        &mut self,
        tree: &mut Tree,
        event: Event,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        _renderer: &Renderer,
        _clipboard: &mut dyn Clipboard,
        shell: &mut Shell<'_, Message>,
        _viewport: &Rectangle,
    ) -> iced::event::Status {
        let bounds = layout.bounds();
        let state = tree.state.downcast_mut::<State>();
        let rail = bounds.height;
        let (off, len) = thumb(self.content, self.viewport, self.scroll, rail);

        match event {
            Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left)) => {
                let Some(pos) = cursor.position() else {
                    return iced::event::Status::Ignored;
                };
                if !bounds.contains(pos) {
                    return iced::event::Status::Ignored;
                }
                let y = pos.y - bounds.y;
                if y >= off && y <= off + len {
                    state.dragging = Some(y - off);
                } else {
                    let thumb_y = (y - len / 2.0).clamp(0.0, (rail - len).max(0.0));
                    state.dragging = Some(y - thumb_y);
                    shell.publish((self.on_scroll)(scroll_from_rail(
                        self.content,
                        self.viewport,
                        thumb_y,
                        rail,
                        SCROLL_HANDLE_MIN,
                    )));
                }
                iced::event::Status::Captured
            }
            Event::Mouse(mouse::Event::CursorMoved { .. }) => {
                let Some(grab) = state.dragging else {
                    return iced::event::Status::Ignored;
                };
                let Some(pos) = cursor.position() else {
                    return iced::event::Status::Ignored;
                };
                let y = pos.y - bounds.y;
                let thumb_y = (y - grab).clamp(0.0, (rail - len).max(0.0));
                shell.publish((self.on_scroll)(scroll_from_rail(
                    self.content,
                    self.viewport,
                    thumb_y,
                    rail,
                    SCROLL_HANDLE_MIN,
                )));
                iced::event::Status::Captured
            }
            Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left)) => {
                if state.dragging.take().is_some() {
                    iced::event::Status::Captured
                } else {
                    iced::event::Status::Ignored
                }
            }
            Event::Mouse(mouse::Event::WheelScrolled { delta }) => {
                if !cursor.is_over(bounds) {
                    return iced::event::Status::Ignored;
                }
                let dy = match delta {
                    mouse::ScrollDelta::Lines { y, .. } => -y * 24.0,
                    mouse::ScrollDelta::Pixels { y, .. } => -y,
                };
                let max_scroll = (self.content - self.viewport).max(0.0);
                shell.publish((self.on_scroll)((self.scroll + dy).clamp(0.0, max_scroll)));
                iced::event::Status::Captured
            }
            _ => iced::event::Status::Ignored,
        }
    }

    fn mouse_interaction(
        &self,
        tree: &Tree,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        _viewport: &Rectangle,
        _renderer: &Renderer,
    ) -> mouse::Interaction {
        let state = tree.state.downcast_ref::<State>();
        if state.dragging.is_some() {
            return mouse::Interaction::Grabbing;
        }
        let bounds = layout.bounds();
        let Some(pos) = cursor.position() else {
            return mouse::Interaction::default();
        };
        if !bounds.contains(pos) {
            return mouse::Interaction::default();
        }
        let (off, len) = thumb(self.content, self.viewport, self.scroll, bounds.height);
        let y = pos.y - bounds.y;
        if y >= off && y <= off + len {
            mouse::Interaction::Grab
        } else {
            mouse::Interaction::Pointer
        }
    }

    fn draw(
        &self,
        tree: &Tree,
        renderer: &mut Renderer,
        theme: &iced::Theme,
        _style: &renderer::Style,
        layout: Layout<'_>,
        cursor: mouse::Cursor,
        _viewport: &Rectangle,
    ) {
        let bounds = layout.bounds();
        let (off, len) = thumb(self.content, self.viewport, self.scroll, bounds.height);
        let thumb_bounds = Rectangle {
            x: bounds.x,
            y: bounds.y + off,
            width: bounds.width,
            height: len,
        };
        let state = tree.state.downcast_ref::<State>();
        let dragging = state.dragging.is_some();
        let hovered = cursor
            .position()
            .is_some_and(|pos| thumb_bounds.contains(pos));
        let (track, thumb_color) = rail_colors(theme, hovered, dragging);
        let radius = SCROLL_RADIUS.into();
        renderer.fill_quad(
            renderer::Quad {
                bounds,
                border: iced::Border {
                    color: Color::TRANSPARENT,
                    width: 0.0,
                    radius,
                },
                ..renderer::Quad::default()
            },
            Background::Color(track),
        );
        if len <= 0.0 {
            return;
        }
        renderer.fill_quad(
            renderer::Quad {
                bounds: thumb_bounds,
                border: iced::Border {
                    color: Color::TRANSPARENT,
                    width: 0.0,
                    radius,
                },
                ..renderer::Quad::default()
            },
            Background::Color(thumb_color),
        );
    }
}

impl<'a, Message: 'a> From<ScrollRail<'a, Message>> for Element<'a, Message> {
    fn from(value: ScrollRail<'a, Message>) -> Self {
        Self::new(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use iced::widget::scrollable::{self, Status};

    #[test]
    fn rail_matches_iced_default_chrome() {
        assert_eq!(SCROLL_RAIL_WIDTH, 10.0);
        assert_eq!(SCROLL_RADIUS, 2.0);
        assert_eq!(SCROLL_HANDLE_MIN, 24.0);
        let theme = crate::theme::iced_theme("textual-dark");
        let (track, thumb) = rail_colors(&theme, false, false);
        let active = scrollable::default(&theme, Status::Active);
        assert_eq!(
            Some(Background::Color(track)),
            active.vertical_rail.background
        );
        assert_eq!(thumb, active.vertical_rail.scroller.color);
        let (h_track, h_thumb) = rail_colors(&theme, true, false);
        let hovered = scrollable::default(
            &theme,
            Status::Hovered {
                is_horizontal_scrollbar_hovered: false,
                is_vertical_scrollbar_hovered: true,
            },
        );
        assert_eq!(h_track, track);
        assert_eq!(h_thumb, hovered.vertical_rail.scroller.color);
        assert_ne!(h_thumb, thumb);
        let (_, d_thumb) = rail_colors(&theme, true, true);
        let dragged = scrollable::default(
            &theme,
            Status::Dragged {
                is_horizontal_scrollbar_dragged: false,
                is_vertical_scrollbar_dragged: true,
            },
        );
        assert_eq!(d_thumb, dragged.vertical_rail.scroller.color);
        assert_ne!(d_thumb, thumb);
    }
}
